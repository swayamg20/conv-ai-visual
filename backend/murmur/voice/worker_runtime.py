"""LiveKit request, room, and server composition for Voice V2 jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from livekit import rtc
from livekit.agents import AgentServer, AutoSubscribe, JobContext, JobRequest

from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.contracts import EventType
from murmur.voice.profile import ProfilePreflight, VoiceProfileRegistry
from murmur.voice.worker_authorization import VoiceJobAuthorizer
from murmur.voice.worker_contracts import (
    AgentRepository,
    AuthorizedVoiceJob,
    SessionRepository,
    VoiceSessionLifecycleError,
    VoiceWorkerSettings,
)
from murmur.voice.worker_events import AgentSessionEventBridge, VoiceEventChannel
from murmur.voice.worker_session import (
    AgentSessionFactory,
    AgentSessionOwner,
    livekit_session_factory,
)


class ReadyPublisher(Protocol):
    async def __call__(
        self,
        ctx: JobContext,
        authorized: AuthorizedVoiceJob,
        preflight: ProfilePreflight,
    ) -> None: ...


async def publish_livekit_ready(
    ctx: JobContext,
    authorized: AuthorizedVoiceJob,
    preflight: ProfilePreflight,
) -> None:
    """Compatibility helper for callers that only need one targeted Ready event."""
    channel = VoiceEventChannel(
        ctx.room.local_participant,
        authorized.metadata,
    )
    try:
        await channel.activate(preflight)
    finally:
        await channel.close()


@dataclass(frozen=True)
class VoiceJobRequestHandler:
    """Pickle-stable request admission callable registered with LiveKit."""

    authorizer: VoiceJobAuthorizer
    profiles: VoiceProfileRegistry
    settings: VoiceWorkerSettings

    async def __call__(self, request: JobRequest) -> None:
        try:
            authorized = await self.authorizer.authorize(request.job)
            await asyncio.wait_for(
                self.profiles.preflight(authorized.profile_scope),
                timeout=self.settings.preflight_timeout_seconds,
            )
        except Exception:
            await request.reject(terminate=True)
            return
        await request.accept(
            name="Murmur voice agent",
            identity=authorized.metadata.agent_participant_identity,
        )


def build_request_handler(
    authorizer: VoiceJobAuthorizer,
    profiles: VoiceProfileRegistry,
    settings: VoiceWorkerSettings,
) -> Callable[[JobRequest], Awaitable[None]]:
    """Reject bad assignments and unavailable profiles before LiveKit accepts them."""

    return VoiceJobRequestHandler(authorizer, profiles, settings)


async def wait_for_microphone_input(
    room: rtc.Room,
    participant: rtc.RemoteParticipant,
    participant_identity: str,
) -> None:
    """Wait race-safely for a subscribed microphone from the signed participant."""
    ready = asyncio.Event()

    def has_microphone() -> bool:
        return any(
            publication.source == rtc.TrackSource.SOURCE_MICROPHONE
            and publication.track is not None
            for publication in participant.track_publications.values()
        )

    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        remote_participant: rtc.RemoteParticipant,
    ) -> None:
        if (
            remote_participant.identity == participant_identity
            and publication.source == rtc.TrackSource.SOURCE_MICROPHONE
            and track is not None
        ):
            ready.set()

    room.on("track_subscribed", on_track_subscribed)
    try:
        # Register first, then inspect current state so publication between the
        # participant wait and listener setup cannot be missed.
        if has_microphone():
            return
        await ready.wait()
    finally:
        room.off("track_subscribed", on_track_subscribed)


@dataclass(frozen=True)
class VoiceJobEntrypoint:
    """Module-level process entrypoint that LiveKit can serialize under spawn."""

    authorizer: VoiceJobAuthorizer
    profiles: VoiceProfileRegistry
    settings: VoiceWorkerSettings
    session_factory: AgentSessionFactory = livekit_session_factory
    ready_publisher: ReadyPublisher | None = None

    async def __call__(self, ctx: JobContext) -> None:
        # Re-authorize after assignment: session/agent ownership can change in the
        # gap between availability acceptance and process entrypoint execution.
        authorized = await self.authorizer.authorize(ctx.job)
        preflight, prepared = await asyncio.wait_for(
            self.profiles.prepare(authorized.profile_scope),
            timeout=self.settings.preflight_timeout_seconds,
        )
        owner: AgentSessionOwner | None = None
        event_channel: VoiceEventChannel | None = None
        event_bridge: AgentSessionEventBridge | None = None
        event_failure_monitor: asyncio.Task[None] | None = None
        session_closed: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        cleanup_lock = asyncio.Lock()

        async def cleanup() -> None:
            async with cleanup_lock:
                if event_bridge is not None:
                    event_bridge.close()
                if (
                    event_failure_monitor is not None
                    and event_failure_monitor is not asyncio.current_task()
                ):
                    event_failure_monitor.cancel()
                    with suppress(asyncio.CancelledError):
                        await event_failure_monitor
                if event_channel is not None:
                    await event_channel.close()
                if owner is not None:
                    await owner.close()

        def observe_session_close(event: object) -> None:
            if event_channel is not None and not event_channel.activated:
                event_channel.fail(
                    VoiceSessionLifecycleError("voice agent session closed before readiness")
                )
            if not session_closed.done():
                session_closed.set_result(event)

        async def wait_for_session_close() -> object:
            return await asyncio.shield(session_closed)

        async def terminate_on_runtime_failure() -> None:
            assert event_channel is not None
            channel_failure = asyncio.create_task(event_channel.wait_for_failure())
            session_terminal = asyncio.create_task(wait_for_session_close())
            try:
                done, _ = await asyncio.wait(
                    (channel_failure, session_terminal),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if channel_failure in done:
                    with suppress(VoiceSessionLifecycleError):
                        channel_failure.result()
                    ctx.shutdown(reason="voice event channel failed")
                    return

                close_event = session_terminal.result()
                reason = getattr(getattr(close_event, "reason", None), "value", None)
                if reason in {"job_shutdown", "user_initiated"}:
                    return
                try:
                    event_channel.emit(
                        EventType.AGENT_UNAVAILABLE,
                        {
                            "code": "agent_session_closed",
                            "message": "Voice agent session ended. Start a fresh voice call.",
                            "retryable": True,
                        },
                    )
                    await asyncio.wait_for(
                        event_channel.wait_for_idle(),
                        timeout=self.settings.event_publish_timeout_seconds,
                    )
                except (TimeoutError, VoiceSessionLifecycleError):
                    pass
                ctx.shutdown(reason="voice agent session closed")
            finally:
                for task in (channel_failure, session_terminal):
                    task.cancel()
                await asyncio.gather(
                    channel_failure,
                    session_terminal,
                    return_exceptions=True,
                )

        try:
            owner = AgentSessionOwner(
                prepared,
                session_factory=self.session_factory,
                cleanup_timeout_seconds=self.settings.cleanup_timeout_seconds,
                interruption_timeout_seconds=self.settings.interruption_timeout_seconds,
            )
            ctx.add_shutdown_callback(cleanup)
            await asyncio.wait_for(
                ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY),
                timeout=self.settings.connect_timeout_seconds,
            )
            participant = await asyncio.wait_for(
                ctx.wait_for_participant(
                    identity=authorized.metadata.participant_identity,
                ),
                timeout=self.settings.participant_wait_timeout_seconds,
            )
            await asyncio.wait_for(
                wait_for_microphone_input(
                    ctx.room,
                    participant,
                    authorized.metadata.participant_identity,
                ),
                timeout=self.settings.input_wait_timeout_seconds,
            )
            if self.ready_publisher is None:
                event_channel = VoiceEventChannel(
                    ctx.room.local_participant,
                    authorized.metadata,
                    publish_timeout_seconds=self.settings.event_publish_timeout_seconds,
                )
                event_bridge = AgentSessionEventBridge(
                    owner.session,
                    event_channel,
                    on_session_closed=observe_session_close,
                )
                event_bridge.bind()
            await asyncio.wait_for(
                owner.start(
                    room=ctx.room,
                    participant_identity=authorized.metadata.participant_identity,
                ),
                timeout=self.settings.session_start_timeout_seconds,
            )
            if self.ready_publisher is None:
                assert event_channel is not None
                # AgentSession can queue a public ``close`` callback while
                # ``start`` is completing. Give those callbacks one event-loop
                # turn before Ready becomes the call's linearization point.
                await asyncio.sleep(0)
                if session_closed.done():
                    raise VoiceSessionLifecycleError("voice agent session closed before readiness")
                # ``activate`` performs its usability check and enqueues Ready
                # synchronously before its first await. Its publisher already
                # has the configured per-write timeout, so wrapping it in a
                # second task would reopen a close-before-Ready scheduling gap.
                async with asyncio.timeout(self.settings.event_publish_timeout_seconds):
                    await event_channel.activate(preflight)
                event_failure_monitor = asyncio.create_task(
                    terminate_on_runtime_failure(),
                    name=f"voice-event-failure:{authorized.metadata.voice_call_id}",
                )
            else:
                await asyncio.wait_for(
                    self.ready_publisher(ctx, authorized, preflight),
                    timeout=self.settings.event_publish_timeout_seconds,
                )
        except BaseException:
            if owner is not None:
                await cleanup()
            elif prepared.close_callback is not None:
                await asyncio.wait_for(
                    prepared.close_callback(),
                    timeout=self.settings.cleanup_timeout_seconds,
                )
            raise


def build_entrypoint(
    authorizer: VoiceJobAuthorizer,
    profiles: VoiceProfileRegistry,
    settings: VoiceWorkerSettings,
    *,
    session_factory: AgentSessionFactory = livekit_session_factory,
    ready_publisher: ReadyPublisher | None = None,
) -> Callable[[JobContext], Awaitable[None]]:
    """Build the job entrypoint with injectable, provider-free test seams."""

    return VoiceJobEntrypoint(
        authorizer,
        profiles,
        settings,
        session_factory=session_factory,
        ready_publisher=ready_publisher,
    )


def build_agent_server(
    settings: VoiceWorkerSettings,
    profiles: VoiceProfileRegistry,
    *,
    session_repo: SessionRepository = SessionRepo,
    agent_repo: AgentRepository = AgentRepo,
    session_factory: AgentSessionFactory = livekit_session_factory,
    ready_publisher: ReadyPublisher | None = None,
    server_factory: Callable[..., AgentServer] = AgentServer,
    clock: Callable[[], datetime] | None = None,
) -> AgentServer:
    """Register exactly one named RTC session on a LiveKit 1.6.9 server."""
    authorizer = VoiceJobAuthorizer(
        settings,
        session_repo=session_repo,
        agent_repo=agent_repo,
        clock=clock,
    )
    server = server_factory(
        shutdown_process_timeout=settings.cleanup_timeout_seconds,
        # Keep exactly one process warm. The one-job load gate still bounds
        # concurrent calls, while avoiding a multi-second first-call spawn after
        # the browser has already granted and published its microphone path.
        num_idle_processes=1,
        load_fnc=single_job_load,
        load_threshold=0.5,
        max_retry=2,
    )
    server.rtc_session(
        build_entrypoint(
            authorizer,
            profiles,
            settings,
            session_factory=session_factory,
            ready_publisher=ready_publisher,
        ),
        agent_name=settings.worker_name,
        on_request=build_request_handler(authorizer, profiles, settings),
    )
    return server


def single_job_load(server: AgentServer) -> float:
    """Expose one active/reserved job as full to the LiveKit dispatcher."""
    return 1.0 if server.active_jobs else 0.0

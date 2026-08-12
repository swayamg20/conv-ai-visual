"""LiveKit request, room, and server composition for Voice V2 jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from livekit import rtc
from livekit.agents import AgentServer, AutoSubscribe, JobContext, JobRequest

from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.contracts import EventEnvelope
from murmur.voice.profile import ProfilePreflight, VoiceProfileRegistry
from murmur.voice.worker_authorization import VoiceJobAuthorizer
from murmur.voice.worker_contracts import (
    AgentRepository,
    AuthorizedVoiceJob,
    SessionRepository,
    VoiceWorkerSettings,
)
from murmur.voice.worker_session import (
    AgentSessionFactory,
    AgentSessionOwner,
    livekit_session_factory,
)

_CORE_READY_COMPONENTS = ("worker", "input", "output", "event_channel")


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
    """Publish Ready only after profile preflight, room connect, and session start."""
    required = tuple(dict.fromkeys((*_CORE_READY_COMPONENTS, *preflight.required_components)))
    ready = tuple(dict.fromkeys((*_CORE_READY_COMPONENTS, *preflight.ready_components)))
    event = EventEnvelope(
        event_id="event-" + uuid4().hex,
        event_type="agent_ready",
        trace_id=authorized.metadata.trace_id,
        voice_call_id=authorized.metadata.voice_call_id,
        session_id=authorized.metadata.session_id,
        producer_id=authorized.metadata.worker_name,
        producer_sequence=1,
        emitted_at=datetime.now(UTC),
        payload={
            "profile_id": authorized.metadata.profile_id,
            "required_components": required,
            "ready_components": ready,
        },
    )
    await ctx.room.local_participant.publish_data(
        event.model_dump_json(exclude_none=True),
        reliable=True,
        topic=authorized.metadata.event_topic,
    )


def build_request_handler(
    authorizer: VoiceJobAuthorizer,
    profiles: VoiceProfileRegistry,
    settings: VoiceWorkerSettings,
) -> Callable[[JobRequest], Awaitable[None]]:
    """Reject bad assignments and unavailable profiles before LiveKit accepts them."""

    async def request_handler(request: JobRequest) -> None:
        try:
            authorized = await authorizer.authorize(request.job)
            await asyncio.wait_for(
                profiles.preflight(authorized.profile_scope),
                timeout=settings.preflight_timeout_seconds,
            )
        except Exception:
            await request.reject(terminate=True)
            return
        await request.accept(
            name="Murmur voice agent",
            identity=authorized.metadata.agent_participant_identity,
        )

    return request_handler


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


def build_entrypoint(
    authorizer: VoiceJobAuthorizer,
    profiles: VoiceProfileRegistry,
    settings: VoiceWorkerSettings,
    *,
    session_factory: AgentSessionFactory = livekit_session_factory,
    ready_publisher: ReadyPublisher = publish_livekit_ready,
) -> Callable[[JobContext], Awaitable[None]]:
    """Build the job entrypoint with injectable, provider-free test seams."""

    async def entrypoint(ctx: JobContext) -> None:
        # Re-authorize after assignment: session/agent ownership can change in the
        # gap between availability acceptance and process entrypoint execution.
        authorized = await authorizer.authorize(ctx.job)
        preflight, prepared = await asyncio.wait_for(
            profiles.prepare(authorized.profile_scope),
            timeout=settings.preflight_timeout_seconds,
        )
        owner: AgentSessionOwner | None = None
        try:
            owner = AgentSessionOwner(
                prepared,
                session_factory=session_factory,
                cleanup_timeout_seconds=settings.cleanup_timeout_seconds,
                interruption_timeout_seconds=settings.interruption_timeout_seconds,
            )
            ctx.add_shutdown_callback(owner.close)
            await asyncio.wait_for(
                ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY),
                timeout=settings.connect_timeout_seconds,
            )
            participant = await asyncio.wait_for(
                ctx.wait_for_participant(
                    identity=authorized.metadata.participant_identity,
                ),
                timeout=settings.participant_wait_timeout_seconds,
            )
            await asyncio.wait_for(
                wait_for_microphone_input(
                    ctx.room,
                    participant,
                    authorized.metadata.participant_identity,
                ),
                timeout=settings.input_wait_timeout_seconds,
            )
            await asyncio.wait_for(
                owner.start(
                    room=ctx.room,
                    participant_identity=authorized.metadata.participant_identity,
                ),
                timeout=settings.session_start_timeout_seconds,
            )
            await asyncio.wait_for(
                ready_publisher(ctx, authorized, preflight),
                timeout=settings.event_publish_timeout_seconds,
            )
        except BaseException:
            if owner is not None:
                await owner.close()
            elif prepared.close_callback is not None:
                await asyncio.wait_for(
                    prepared.close_callback(),
                    timeout=settings.cleanup_timeout_seconds,
                )
            raise

    return entrypoint


def build_agent_server(
    settings: VoiceWorkerSettings,
    profiles: VoiceProfileRegistry,
    *,
    session_repo: SessionRepository = SessionRepo,
    agent_repo: AgentRepository = AgentRepo,
    session_factory: AgentSessionFactory = livekit_session_factory,
    ready_publisher: ReadyPublisher = publish_livekit_ready,
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
        num_idle_processes=0,
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

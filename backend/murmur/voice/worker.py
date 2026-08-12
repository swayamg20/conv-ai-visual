"""Minimal, fail-closed LiveKit Agents worker for Murmur Voice V2.

The module binds the pinned LiveKit Agents 1.6.9 job APIs to Murmur's signed
bootstrap contract.  Provider plugins are injected through ``profile.py``;
this module never uses managed-inference model strings.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Protocol, TypeVar
from uuid import uuid4

from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, AutoSubscribe, JobContext, JobRequest
from livekit.agents.voice.room_io import RoomOptions

from murmur.core.config import config
from murmur.persistence.models import AgentModel, SessionModel
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.blocking import (
    BoundedSyncRunner,
    BoundedSyncRunnerUnavailable,
    default_repository_runner,
)
from murmur.voice.bootstrap import (
    VOICE_V2_EVENT_TOPIC,
    VOICE_V2_RUNTIME,
    verify_signed_metadata,
)
from murmur.voice.contracts import EventEnvelope
from murmur.voice.profile import (
    PreparedVoiceProfile,
    ProfilePreflight,
    UnavailableVoiceProfileProvider,
    VoiceProfileRegistry,
    VoiceProfileScope,
)

_METADATA_VALUE_MAX_LENGTH = 128
_SAFE_METADATA_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_CORE_READY_COMPONENTS = ("worker", "input", "output", "event_channel")
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_WorkerResult = TypeVar("_WorkerResult")


class VoiceWorkerError(RuntimeError):
    """Base class for expected fail-closed worker errors."""


class VoiceJobRejected(VoiceWorkerError):
    """Signed job metadata or authoritative ownership is invalid."""


class VoiceSessionLifecycleError(VoiceWorkerError):
    """The one-session runtime could not start, interrupt, or close safely."""


@dataclass(frozen=True)
class VoiceWorkerSettings:
    signing_secret: str
    environment: str
    profile_id: str
    worker_name: str
    event_topic: str
    job_metadata_ttl_seconds: int = 300
    job_metadata_clock_skew_seconds: int = 30
    repository_timeout_seconds: float = 2.0
    preflight_timeout_seconds: float = 5.0
    connect_timeout_seconds: float = 10.0
    participant_wait_timeout_seconds: float = 15.0
    input_wait_timeout_seconds: float = 10.0
    session_start_timeout_seconds: float = 10.0
    event_publish_timeout_seconds: float = 3.0
    cleanup_timeout_seconds: float = 5.0
    interruption_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        required = {
            "signing_secret": self.signing_secret,
            "environment": self.environment,
            "profile_id": self.profile_id,
            "worker_name": self.worker_name,
            "event_topic": self.event_topic,
        }
        missing = sorted(name for name, value in required.items() if not value.strip())
        if missing:
            raise ValueError("missing Voice V2 worker settings: " + ", ".join(missing))
        for name in ("environment", "profile_id", "worker_name", "event_topic"):
            value = getattr(self, name)
            if (
                len(value) > _METADATA_VALUE_MAX_LENGTH
                or _SAFE_METADATA_VALUE.fullmatch(value) is None
            ):
                raise ValueError(f"Voice V2 worker {name} is not a valid contract identifier")
        if self.event_topic != VOICE_V2_EVENT_TOPIC:
            raise ValueError(f"Voice V2 worker event_topic must be {VOICE_V2_EVENT_TOPIC}")
        if len(self.signing_secret.encode("utf-8")) < 32:
            raise ValueError("Voice V2 signing secret must contain at least 32 bytes")
        if (
            isinstance(self.job_metadata_ttl_seconds, bool)
            or not isinstance(self.job_metadata_ttl_seconds, int)
            or not 30 <= self.job_metadata_ttl_seconds <= 900
        ):
            raise ValueError("Voice V2 job metadata TTL must be between 30 and 900 seconds")
        if (
            isinstance(self.job_metadata_clock_skew_seconds, bool)
            or not isinstance(self.job_metadata_clock_skew_seconds, int)
            or not 0 <= self.job_metadata_clock_skew_seconds <= 60
            or self.job_metadata_clock_skew_seconds > self.job_metadata_ttl_seconds
        ):
            raise ValueError(
                "Voice V2 job metadata clock skew must be between 0 and 60 seconds "
                "and no greater than its TTL"
            )
        if (
            isinstance(self.repository_timeout_seconds, bool)
            or not math.isfinite(self.repository_timeout_seconds)
            or self.repository_timeout_seconds <= 0
            or self.repository_timeout_seconds > 30
        ):
            raise ValueError("Voice V2 repository timeout must be between 0 and 30 seconds")
        for name, value in (
            ("preflight", self.preflight_timeout_seconds),
            ("connect", self.connect_timeout_seconds),
            ("session-start", self.session_start_timeout_seconds),
            ("event-publish", self.event_publish_timeout_seconds),
        ):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 30:
                raise ValueError(f"Voice V2 {name} timeout must be between 0 and 30 seconds")
        if (
            isinstance(self.participant_wait_timeout_seconds, bool)
            or not math.isfinite(self.participant_wait_timeout_seconds)
            or not 0 < self.participant_wait_timeout_seconds <= 60
        ):
            raise ValueError("Voice V2 participant wait timeout must be between 0 and 60 seconds")
        if (
            isinstance(self.input_wait_timeout_seconds, bool)
            or not math.isfinite(self.input_wait_timeout_seconds)
            or not 0 < self.input_wait_timeout_seconds <= 60
        ):
            raise ValueError("Voice V2 input wait timeout must be between 0 and 60 seconds")
        if self.cleanup_timeout_seconds <= 0 or self.interruption_timeout_seconds <= 0:
            raise ValueError("Voice V2 lifecycle timeouts must be positive")


@dataclass(frozen=True)
class VoiceJobMetadata:
    agent_id: str
    agent_participant_identity: str
    environment: str
    event_topic: str
    job_expires_at: int
    job_issued_at: int
    participant_identity: str
    profile_id: str
    room_name: str
    runtime: str
    session_id: str
    trace_id: str
    user_id: str
    voice_call_id: str
    worker_name: str


@dataclass(frozen=True)
class AuthorizedVoiceJob:
    metadata: VoiceJobMetadata
    session: SessionModel
    agent: AgentModel

    @property
    def profile_scope(self) -> VoiceProfileScope:
        return VoiceProfileScope(
            profile_id=self.metadata.profile_id,
            user_id=self.metadata.user_id,
            session_id=self.metadata.session_id,
            agent_id=self.metadata.agent_id,
            voice_call_id=self.metadata.voice_call_id,
            trace_id=self.metadata.trace_id,
            system_prompt=self.agent.system_prompt,
        )


class SessionRepository(Protocol):
    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None: ...


class AgentRepository(Protocol):
    @staticmethod
    def get_by_id(agent_id: str) -> AgentModel | None: ...


class JobDescriptor(Protocol):
    metadata: str
    agent_name: str

    @property
    def room(self) -> object: ...


class OwnedAgentSession(Protocol):
    async def start(
        self,
        agent: Agent,
        *,
        room: object,
        room_options: RoomOptions,
    ) -> object: ...

    def interrupt(self, *, force: bool = False) -> Awaitable[None]: ...

    def shutdown(self, *, drain: bool = True) -> None: ...

    async def aclose(self) -> None: ...


class AgentSessionFactory(Protocol):
    def __call__(self, prepared: PreparedVoiceProfile) -> tuple[OwnedAgentSession, Agent]: ...


class ReadyPublisher(Protocol):
    async def __call__(
        self,
        ctx: JobContext,
        authorized: AuthorizedVoiceJob,
        preflight: ProfilePreflight,
    ) -> None: ...


class VoiceJobAuthorizer:
    """Verify assignment integrity and reload ownership from authoritative repos."""

    def __init__(
        self,
        settings: VoiceWorkerSettings,
        *,
        session_repo: SessionRepository = SessionRepo,
        agent_repo: AgentRepository = AgentRepo,
        repository_runner: BoundedSyncRunner = default_repository_runner,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._session_repo = session_repo
        self._agent_repo = agent_repo
        self._repository_runner = repository_runner
        self._clock = clock or (lambda: datetime.now(UTC))

    async def authorize(self, job: JobDescriptor) -> AuthorizedVoiceJob:
        metadata = parse_job_metadata(job.metadata, self._settings.signing_secret)
        room = getattr(job, "room", None)
        room_name = getattr(room, "name", None)
        if room_name != metadata.room_name:
            raise VoiceJobRejected("voice job room does not match signed metadata")
        if metadata.profile_id != self._settings.profile_id:
            raise VoiceJobRejected("voice job profile does not match this worker")
        if metadata.worker_name != self._settings.worker_name:
            raise VoiceJobRejected("voice job worker does not match this worker")
        if metadata.event_topic != self._settings.event_topic:
            raise VoiceJobRejected("voice job event topic does not match this worker")
        if job.agent_name != self._settings.worker_name:
            raise VoiceJobRejected("LiveKit dispatch targets a different worker")
        if metadata.environment != self._settings.environment:
            raise VoiceJobRejected("voice job environment does not match this worker")
        if metadata.runtime != VOICE_V2_RUNTIME:
            raise VoiceJobRejected("voice job runtime is unsupported")
        now = self._aware_now_epoch()
        if metadata.job_expires_at <= metadata.job_issued_at:
            raise VoiceJobRejected("voice job metadata time window is invalid")
        if (
            metadata.job_expires_at - metadata.job_issued_at
            > self._settings.job_metadata_ttl_seconds
        ):
            raise VoiceJobRejected("voice job metadata time window is overlong")
        clock_skew = self._settings.job_metadata_clock_skew_seconds
        if metadata.job_issued_at > now + clock_skew:
            raise VoiceJobRejected("voice job metadata was issued in the future")
        if metadata.job_expires_at + clock_skew <= now:
            raise VoiceJobRejected("voice job metadata has expired")

        session = await self._repository_lookup(self._session_repo.get_by_id, metadata.session_id)
        if session is None:
            raise VoiceJobRejected("authoritative voice session was not found")
        agent = await self._repository_lookup(self._agent_repo.get_by_id, metadata.agent_id)
        if agent is None:
            raise VoiceJobRejected("authoritative voice agent was not found")
        if (
            session.id != metadata.session_id
            or session.user_id != metadata.user_id
            or session.agent_id != metadata.agent_id
            or agent.id != metadata.agent_id
            or agent.user_id != metadata.user_id
        ):
            raise VoiceJobRejected("authoritative voice ownership does not match the assignment")
        return AuthorizedVoiceJob(metadata=metadata, session=session, agent=agent)

    async def _repository_lookup(
        self,
        lookup: Callable[[str], _WorkerResult],
        key: str,
    ) -> _WorkerResult:
        try:
            return await self._repository_runner.run(
                lookup,
                key,
                timeout_seconds=self._settings.repository_timeout_seconds,
            )
        except TimeoutError as exc:
            raise VoiceJobRejected("authoritative voice repository lookup timed out") from exc
        except BoundedSyncRunnerUnavailable as exc:
            raise VoiceJobRejected("authoritative voice repository capacity is exhausted") from exc
        except Exception as exc:
            raise VoiceJobRejected("authoritative voice repository lookup failed") from exc

    def _aware_now_epoch(self) -> int:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise VoiceJobRejected("Voice V2 worker clock must return an aware timestamp")
        return int(now.astimezone(UTC).timestamp())


class AgentSessionOwner:
    """Own exactly one LiveKit ``AgentSession`` and its bounded lifecycle."""

    def __init__(
        self,
        prepared: PreparedVoiceProfile,
        *,
        session_factory: AgentSessionFactory,
        cleanup_timeout_seconds: float,
        interruption_timeout_seconds: float,
    ) -> None:
        self._prepared = prepared
        # This is the sole construction point.  No provider/profile object owns a
        # second AgentSession and repeated start calls cannot create another one.
        self._session, self._agent = session_factory(prepared)
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._interruption_timeout_seconds = interruption_timeout_seconds
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._started = False
        self._closed = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self, *, room: object, participant_identity: str) -> None:
        async with self._start_lock:
            if self._closed:
                raise VoiceSessionLifecycleError("voice session owner is already closed")
            if self._started:
                return
            await self._session.start(
                self._agent,
                room=room,
                room_options=RoomOptions(
                    participant_identity=participant_identity,
                    text_input=False,
                ),
            )
            self._started = True

    async def interrupt(self) -> None:
        if not self._started or self._closed:
            raise VoiceSessionLifecycleError("voice session is not running")
        try:
            await asyncio.wait_for(
                self._session.interrupt(force=True),
                timeout=self._interruption_timeout_seconds,
            )
        except TimeoutError as exc:
            self._session.shutdown(drain=False)
            raise VoiceSessionLifecycleError("voice interruption timed out") from exc

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._session.shutdown(drain=False)
            errors: list[Exception] = []

            async def cleanup() -> None:
                try:
                    await self._session.aclose()
                except Exception as exc:
                    errors.append(exc)
                if self._prepared.close_callback is not None:
                    try:
                        await self._prepared.close_callback()
                    except Exception as exc:
                        errors.append(exc)

            try:
                await asyncio.wait_for(cleanup(), timeout=self._cleanup_timeout_seconds)
            except TimeoutError as exc:
                raise VoiceSessionLifecycleError("voice session cleanup timed out") from exc
            if errors:
                raise VoiceSessionLifecycleError(
                    "voice session cleanup did not complete"
                ) from errors[0]


def parse_job_metadata(encoded: str, signing_secret: str) -> VoiceJobMetadata:
    """Strictly verify the purpose-bound HMAC envelope and its exact payload."""
    try:
        payload = verify_signed_metadata(encoded, signing_secret, purpose="job")
    except ValueError as exc:
        raise VoiceJobRejected("voice job metadata signature is invalid") from exc

    expected_keys = {field.name for field in fields(VoiceJobMetadata)}
    if set(payload) != expected_keys:
        raise VoiceJobRejected("voice job metadata payload has unexpected fields")
    timestamp_fields = {"job_issued_at", "job_expires_at"}
    for key, value in payload.items():
        if key in timestamp_fields:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_SAFE_INTEGER
            ):
                raise VoiceJobRejected(f"voice job metadata field {key} is invalid")
            continue
        if key == "user_id":
            if not isinstance(value, str) or not value or len(value) > _METADATA_VALUE_MAX_LENGTH:
                raise VoiceJobRejected("voice job metadata field user_id is invalid")
            continue
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _METADATA_VALUE_MAX_LENGTH
            or _SAFE_METADATA_VALUE.fullmatch(value) is None
        ):
            raise VoiceJobRejected(f"voice job metadata field {key} is invalid")
    return VoiceJobMetadata(**payload)  # type: ignore[arg-type]


def livekit_session_factory(prepared: PreparedVoiceProfile) -> tuple[OwnedAgentSession, Agent]:
    """Construct one pinned LiveKit Agents 1.6.9 session from direct objects."""
    components = {"stt": prepared.stt, "llm": prepared.llm, "tts": prepared.tts}
    managed = sorted(name for name, component in components.items() if isinstance(component, str))
    if managed:
        raise VoiceSessionLifecycleError(
            "managed-inference model strings are forbidden for: " + ", ".join(managed)
        )
    session = AgentSession(
        stt=prepared.stt,  # type: ignore[arg-type]
        llm=prepared.llm,  # type: ignore[arg-type]
        tts=prepared.tts,  # type: ignore[arg-type]
        vad=prepared.vad,  # type: ignore[arg-type]
    )
    agent = Agent(instructions=prepared.instructions)
    return session, agent


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


async def _wait_for_microphone_input(
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
                _wait_for_microphone_input(
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
        load_fnc=_single_job_load,
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


def _single_job_load(server: AgentServer) -> float:
    """Expose one active/reserved job as full to the LiveKit dispatcher."""
    return 1.0 if server.active_jobs else 0.0


def _default_worker() -> tuple[VoiceWorkerSettings, VoiceProfileRegistry]:
    settings = VoiceWorkerSettings(
        signing_secret=str(getattr(config, "VOICE_V2_SIGNING_SECRET", "") or "").strip(),
        environment=str(getattr(config, "MURMUR_ENVIRONMENT", "") or "").strip(),
        profile_id=str(getattr(config, "VOICE_V2_PROFILE_ID", "") or "").strip(),
        worker_name=str(getattr(config, "VOICE_V2_WORKER_NAME", "") or "").strip(),
        event_topic=VOICE_V2_EVENT_TOPIC,
        job_metadata_ttl_seconds=int(getattr(config, "VOICE_V2_JOB_METADATA_TTL_SECONDS", 300)),
        job_metadata_clock_skew_seconds=int(
            getattr(config, "VOICE_V2_JOB_METADATA_CLOCK_SKEW_SECONDS", 30)
        ),
        repository_timeout_seconds=float(getattr(config, "VOICE_V2_REPOSITORY_TIMEOUT_SECONDS", 2)),
        preflight_timeout_seconds=float(getattr(config, "VOICE_V2_PREFLIGHT_TIMEOUT_SECONDS", 5)),
        connect_timeout_seconds=float(getattr(config, "VOICE_V2_CONNECT_TIMEOUT_SECONDS", 10)),
        participant_wait_timeout_seconds=float(
            getattr(config, "VOICE_V2_PARTICIPANT_WAIT_TIMEOUT_SECONDS", 15)
        ),
        input_wait_timeout_seconds=float(
            getattr(config, "VOICE_V2_INPUT_WAIT_TIMEOUT_SECONDS", 10)
        ),
        session_start_timeout_seconds=float(
            getattr(config, "VOICE_V2_SESSION_START_TIMEOUT_SECONDS", 10)
        ),
        event_publish_timeout_seconds=float(
            getattr(config, "VOICE_V2_EVENT_PUBLISH_TIMEOUT_SECONDS", 3)
        ),
    )
    provider = UnavailableVoiceProfileProvider(
        settings.profile_id,
        "direct Voice V2 provider adapters are not installed or configured",
    )
    return settings, VoiceProfileRegistry({settings.profile_id: provider})


# The LiveKit 1.6.9 CLI discovers this native AgentServer global with:
# ``python -m livekit.agents start backend/murmur/voice/worker.py --dev``.
# Incomplete standalone-worker configuration fails during CLI discovery, before
# the worker can register or accept jobs.  The FastAPI application never imports
# this standalone entrypoint.
server = build_agent_server(*_default_worker())

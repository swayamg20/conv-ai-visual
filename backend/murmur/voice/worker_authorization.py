"""Signed assignment verification and authoritative worker authorization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime
from typing import TypeVar

from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.blocking import (
    BoundedSyncRunner,
    BoundedSyncRunnerUnavailable,
    default_repository_runner,
)
from murmur.voice.bootstrap_contracts import (
    VOICE_V2_RUNTIME,
    is_contract_id,
    verify_signed_metadata,
)
from murmur.voice.worker_contracts import (
    MAX_SAFE_INTEGER,
    METADATA_VALUE_MAX_LENGTH,
    AgentRepository,
    AuthorizedVoiceJob,
    JobDescriptor,
    SessionRepository,
    VoiceJobMetadata,
    VoiceJobRejected,
    VoiceWorkerSettings,
)

_WorkerResult = TypeVar("_WorkerResult")


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
                or not 0 <= value <= MAX_SAFE_INTEGER
            ):
                raise VoiceJobRejected(f"voice job metadata field {key} is invalid")
            continue
        if key == "user_id":
            if not isinstance(value, str) or not value or len(value) > METADATA_VALUE_MAX_LENGTH:
                raise VoiceJobRejected("voice job metadata field user_id is invalid")
            continue
        if not is_contract_id(value):
            raise VoiceJobRejected(f"voice job metadata field {key} is invalid")
    return VoiceJobMetadata(**payload)  # type: ignore[arg-type]


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

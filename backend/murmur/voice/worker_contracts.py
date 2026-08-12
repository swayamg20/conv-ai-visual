"""Validated, SDK-neutral contracts for the Voice V2 worker."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from murmur.persistence.models import AgentModel, SessionModel
from murmur.voice.bootstrap_contracts import (
    VOICE_V2_EVENT_TOPIC,
    VoiceJobMetadata,
    is_contract_id,
)
from murmur.voice.profile import VoiceProfileScope

METADATA_VALUE_MAX_LENGTH = 128
MAX_SAFE_INTEGER = 9_007_199_254_740_991


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
            if not is_contract_id(getattr(self, name)):
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

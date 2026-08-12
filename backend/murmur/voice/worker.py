"""Standalone LiveKit CLI composition for Murmur Voice V2.

The implementation boundaries live in ``worker_*`` modules.  This module
retains the original public import surface and constructs the one CLI server.
"""

from __future__ import annotations

from murmur.core.config import config
from murmur.voice.bootstrap_contracts import VOICE_V2_EVENT_TOPIC, VOICE_V2_RUNTIME
from murmur.voice.profile import UnavailableVoiceProfileProvider, VoiceProfileRegistry
from murmur.voice.worker_authorization import VoiceJobAuthorizer, parse_job_metadata
from murmur.voice.worker_contracts import (
    AgentRepository,
    AuthorizedVoiceJob,
    JobDescriptor,
    SessionRepository,
    VoiceJobMetadata,
    VoiceJobRejected,
    VoiceSessionLifecycleError,
    VoiceWorkerError,
    VoiceWorkerSettings,
)
from murmur.voice.worker_runtime import (
    ReadyPublisher,
    build_agent_server,
    build_entrypoint,
    build_request_handler,
    publish_livekit_ready,
    single_job_load,
    wait_for_microphone_input,
)
from murmur.voice.worker_session import (
    AgentSessionFactory,
    AgentSessionOwner,
    OwnedAgentSession,
    livekit_session_factory,
)

__all__ = [
    "VOICE_V2_EVENT_TOPIC",
    "VOICE_V2_RUNTIME",
    "AgentRepository",
    "AgentSessionFactory",
    "AgentSessionOwner",
    "AuthorizedVoiceJob",
    "JobDescriptor",
    "OwnedAgentSession",
    "ReadyPublisher",
    "SessionRepository",
    "VoiceJobAuthorizer",
    "VoiceJobMetadata",
    "VoiceJobRejected",
    "VoiceSessionLifecycleError",
    "VoiceWorkerError",
    "VoiceWorkerSettings",
    "build_agent_server",
    "build_entrypoint",
    "build_request_handler",
    "livekit_session_factory",
    "parse_job_metadata",
    "publish_livekit_ready",
    "server",
]

# Preserve the private helper names used by existing diagnostics while keeping
# their implementation in the runtime boundary.
_single_job_load = single_job_load
_wait_for_microphone_input = wait_for_microphone_input


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
# the worker can register or accept jobs. The FastAPI application never imports
# this standalone entrypoint.
server = build_agent_server(*_default_worker())

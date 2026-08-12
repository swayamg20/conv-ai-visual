"""Standalone LiveKit CLI composition for Murmur Voice V2.

The implementation boundaries live in ``worker_*`` modules.  This module
retains the original public import surface and constructs the one CLI server.
"""

from __future__ import annotations

from collections.abc import Callable

from murmur.core.config import config
from murmur.voice.bootstrap_contracts import VOICE_V2_EVENT_TOPIC, VOICE_V2_RUNTIME
from murmur.voice.profile import (
    PreparedVoiceProfile,
    ProfileAdmission,
    ProfilePreflight,
    ProfileReadiness,
    ProviderModelReadiness,
    UnavailableVoiceProfileProvider,
    VoiceAPIConnectionPolicy,
    VoiceConnectionPolicy,
    VoiceMediaPolicy,
    VoiceProfileProvider,
    VoiceProfileRegistry,
    VoiceProfileUnavailable,
    VoiceSessionPolicy,
)
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
from murmur.voice.worker_events import AgentSessionEventBridge, VoiceEventChannel
from murmur.voice.worker_runtime import (
    ReadyPublisher,
    VoiceJobEntrypoint,
    VoiceJobRequestHandler,
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
    OwnedRoomIO,
    livekit_session_factory,
)

__all__ = [
    "VOICE_V2_EVENT_TOPIC",
    "VOICE_V2_RUNTIME",
    "AgentRepository",
    "AgentSessionEventBridge",
    "AgentSessionFactory",
    "AgentSessionOwner",
    "AuthorizedVoiceJob",
    "JobDescriptor",
    "OwnedAgentSession",
    "OwnedRoomIO",
    "PreparedVoiceProfile",
    "ProfileAdmission",
    "ProfilePreflight",
    "ProfileReadiness",
    "ProviderModelReadiness",
    "ReadyPublisher",
    "SessionRepository",
    "VoiceAPIConnectionPolicy",
    "VoiceConnectionPolicy",
    "VoiceEventChannel",
    "VoiceJobAuthorizer",
    "VoiceJobEntrypoint",
    "VoiceJobMetadata",
    "VoiceJobRejected",
    "VoiceJobRequestHandler",
    "VoiceMediaPolicy",
    "VoiceSessionLifecycleError",
    "VoiceSessionPolicy",
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


ProfileProviderFactory = Callable[[object], VoiceProfileProvider]


def _default_worker(
    *, provider_factory: ProfileProviderFactory | None = None
) -> tuple[VoiceWorkerSettings, VoiceProfileRegistry]:
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
    provider = _default_profile_provider(settings, provider_factory=provider_factory)
    return settings, VoiceProfileRegistry({settings.profile_id: provider})


def _default_profile_provider(
    settings: VoiceWorkerSettings,
    *,
    provider_factory: ProfileProviderFactory | None,
) -> VoiceProfileProvider:
    """Load the optional direct profile without weakening worker startup safety."""
    factory = provider_factory
    if factory is None:
        try:
            from murmur.voice.provider_profiles.livekit_cascade import (
                build_direct_cascade_provider_from_config,
            )
        except ImportError:
            build_direct_cascade_provider_from_config = None
        factory = build_direct_cascade_provider_from_config
    if factory is None:
        return UnavailableVoiceProfileProvider(
            settings.profile_id,
            "direct Voice V2 provider adapters are not installed",
        )
    try:
        provider = factory(config)
    except (ImportError, ValueError, VoiceProfileUnavailable) as exc:
        reason = str(exc).strip() or "direct Voice V2 provider configuration is unavailable"
        return UnavailableVoiceProfileProvider(settings.profile_id, reason)
    if not callable(getattr(provider, "admit", None)) or not callable(
        getattr(provider, "prepare", None)
    ):
        return UnavailableVoiceProfileProvider(
            settings.profile_id,
            "direct Voice V2 provider factory returned an invalid adapter",
        )
    return provider


# The LiveKit 1.6.9 CLI discovers this native AgentServer global with:
# ``python -m livekit.agents start backend/murmur/voice/worker.py --dev``.
# Incomplete standalone-worker configuration fails during CLI discovery, before
# the worker can register or accept jobs. The FastAPI application never imports
# this standalone entrypoint.
server = build_agent_server(*_default_worker())

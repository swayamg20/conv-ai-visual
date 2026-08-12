"""Loopback-only LiveKit worker composition for deterministic RTC validation."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from murmur.voice.bootstrap_contracts import VOICE_V2_EVENT_TOPIC
from murmur.voice.fake_rtc import (
    FAKE_RTC_PROFILE_ID,
    create_fake_rtc_profile_provider_from_environment,
)
from murmur.voice.profile import VoiceProfileRegistry
from murmur.voice.worker_contracts import VoiceWorkerSettings
from murmur.voice.worker_runtime import build_agent_server


def _loopback_hostname(value: str) -> bool:
    try:
        hostname = urlsplit(value).hostname
    except ValueError:
        return False
    return hostname in {"127.0.0.1", "localhost", "::1"}


def build_e2e_server():
    """Build the native AgentServer only inside the guarded local test topology."""
    if os.getenv("MURMUR_E2E_MODE") != "1":
        raise RuntimeError("voice E2E worker requires MURMUR_E2E_MODE=1")
    if os.getenv("MURMUR_ENVIRONMENT") != "test":
        raise RuntimeError("voice E2E worker requires MURMUR_ENVIRONMENT=test")
    if os.getenv("VOICE_V2_PROFILE_ID") != FAKE_RTC_PROFILE_ID:
        raise RuntimeError("voice E2E worker requires fake-rtc-v1")
    if not _loopback_hostname(os.getenv("LIVEKIT_URL", "")):
        raise RuntimeError("voice E2E worker requires a loopback LiveKit URL")

    settings = VoiceWorkerSettings(
        signing_secret=os.environ["VOICE_V2_SIGNING_SECRET"].strip(),
        environment="test",
        profile_id=FAKE_RTC_PROFILE_ID,
        worker_name=os.environ["VOICE_V2_WORKER_NAME"].strip(),
        event_topic=VOICE_V2_EVENT_TOPIC,
        job_metadata_ttl_seconds=int(os.getenv("VOICE_V2_JOB_METADATA_TTL_SECONDS", "300")),
        job_metadata_clock_skew_seconds=int(
            os.getenv("VOICE_V2_JOB_METADATA_CLOCK_SKEW_SECONDS", "30")
        ),
        repository_timeout_seconds=float(os.getenv("VOICE_V2_REPOSITORY_TIMEOUT_SECONDS", "2")),
        preflight_timeout_seconds=float(os.getenv("VOICE_V2_PREFLIGHT_TIMEOUT_SECONDS", "5")),
        connect_timeout_seconds=float(os.getenv("VOICE_V2_CONNECT_TIMEOUT_SECONDS", "10")),
        participant_wait_timeout_seconds=float(
            os.getenv("VOICE_V2_PARTICIPANT_WAIT_TIMEOUT_SECONDS", "15")
        ),
        input_wait_timeout_seconds=float(os.getenv("VOICE_V2_INPUT_WAIT_TIMEOUT_SECONDS", "10")),
        session_start_timeout_seconds=float(
            os.getenv("VOICE_V2_SESSION_START_TIMEOUT_SECONDS", "10")
        ),
        event_publish_timeout_seconds=float(
            os.getenv("VOICE_V2_EVENT_PUBLISH_TIMEOUT_SECONDS", "3")
        ),
    )
    provider = create_fake_rtc_profile_provider_from_environment()
    return build_agent_server(settings, VoiceProfileRegistry({FAKE_RTC_PROFILE_ID: provider}))


# The pinned LiveKit CLI imports this global from the filesystem entrypoint.
server = build_e2e_server()

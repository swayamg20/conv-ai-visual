"""Provider-neutral profile seam for the self-hosted Voice V2 worker.

This module deliberately does not import LiveKit or provider plugins.  A profile
factory proves that its explicit provider objects are usable, then hands those
objects to the worker.  The worker remains the sole owner of ``AgentSession``.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal, Protocol

from murmur.voice.bootstrap_contracts import is_contract_id


class VoiceProfileError(RuntimeError):
    """Base class for profile selection and preflight failures."""


class VoiceProfileUnavailable(VoiceProfileError):
    """The selected direct-provider profile cannot serve a voice job."""


@dataclass(frozen=True)
class VoiceProfileScope:
    """Trusted, authoritative scope supplied to a profile factory."""

    profile_id: str
    user_id: str
    session_id: str
    agent_id: str
    voice_call_id: str
    trace_id: str
    system_prompt: str


@dataclass(frozen=True)
class ProfileAdmission:
    """Cheap, network-free proof that a profile can accept an assignment.

    Admission deliberately makes no provider-readiness claim.  It proves only
    that the selected profile, local adapter imports, and static configuration
    are present.  ``prepare`` remains the sole authoritative network check.
    """

    profile_id: str
    required_components: tuple[str, ...]
    config_hash: str

    def __post_init__(self) -> None:
        if not is_contract_id(self.profile_id):
            raise ValueError("profile admission profile_id must be a contract identifier")
        _validated_components("required_components", self.required_components)
        _validated_config_hash(self.config_hash)


@dataclass(frozen=True)
class ProviderModelReadiness:
    """Non-secret provider/model identity proven by an authoritative probe."""

    component: str
    provider: str
    model: str

    def __post_init__(self) -> None:
        if not is_contract_id(self.component):
            raise ValueError("provider readiness component must be a contract identifier")
        for name in ("provider", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"provider readiness {name} must be a non-empty string")


@dataclass(frozen=True)
class ProfileReadiness:
    """Authoritative, post-probe readiness attached to prepared provider objects."""

    profile_id: str
    required_components: tuple[str, ...]
    ready_components: tuple[str, ...]
    config_hash: str = field(default_factory=lambda: _legacy_config_hash("legacy-profile"))
    provider_models: tuple[ProviderModelReadiness, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not is_contract_id(self.profile_id):
            raise ValueError("profile readiness profile_id must be a contract identifier")
        required = _validated_components("required_components", self.required_components)
        ready = _validated_components("ready_components", self.ready_components)
        if not set(required).issubset(ready):
            raise ValueError("profile preflight did not ready every required component")
        _validated_config_hash(self.config_hash)
        if any(
            not isinstance(descriptor, ProviderModelReadiness)
            for descriptor in self.provider_models
        ):
            raise ValueError("profile readiness provider_models are invalid")
        described = [descriptor.component for descriptor in self.provider_models]
        if len(described) != len(set(described)):
            raise ValueError("profile readiness provider components must be unique")
        if not set(described).issubset(ready):
            raise ValueError("profile readiness described a component that is not ready")
        if any(
            not isinstance(limitation, str) or not limitation.strip() or len(limitation) > 512
            for limitation in self.limitations
        ):
            raise ValueError("profile readiness limitations must be non-empty strings")


# Compatibility name retained for the already-published Ready/event interface.
# It now means authoritative post-prepare readiness, never request admission.
ProfilePreflight = ProfileReadiness


@dataclass(frozen=True)
class VoiceAPIConnectionPolicy:
    """One provider API attempt budget translated only at the SDK boundary."""

    max_retry: int = 1
    retry_interval_seconds: float = 0.25
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if isinstance(self.max_retry, bool) or not isinstance(self.max_retry, int):
            raise ValueError("voice provider max_retry must be an integer")
        if not 0 <= self.max_retry <= 1:
            raise ValueError("voice provider max_retry must be zero or one")
        for name in ("retry_interval_seconds", "timeout_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"voice provider {name} must be a finite non-negative number")
        if self.retry_interval_seconds > 1:
            raise ValueError("voice provider retry interval must not exceed one second")
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("voice provider timeout must be between zero and 15 seconds")


@dataclass(frozen=True)
class VoiceConnectionPolicy:
    """Bounded STT/LLM/TTS connection policy without multiplicative retries."""

    stt: VoiceAPIConnectionPolicy = field(
        default_factory=lambda: VoiceAPIConnectionPolicy(timeout_seconds=5.0)
    )
    llm: VoiceAPIConnectionPolicy = field(
        default_factory=lambda: VoiceAPIConnectionPolicy(timeout_seconds=8.0)
    )
    tts: VoiceAPIConnectionPolicy = field(
        default_factory=lambda: VoiceAPIConnectionPolicy(timeout_seconds=5.0)
    )
    max_unrecoverable_errors: int = 1

    def __post_init__(self) -> None:
        for name in ("stt", "llm", "tts"):
            if not isinstance(getattr(self, name), VoiceAPIConnectionPolicy):
                raise ValueError(f"voice connection {name} policy is invalid")
        if (
            isinstance(self.max_unrecoverable_errors, bool)
            or not isinstance(self.max_unrecoverable_errors, int)
            or not 1 <= self.max_unrecoverable_errors <= 3
        ):
            raise ValueError("voice max_unrecoverable_errors must be between one and three")


@dataclass(frozen=True)
class VoiceSessionPolicy:
    """Provider-neutral turn/session behavior for deterministic voice profiles.

    The policy describes Murmur semantics rather than LiveKit constructor keys.
    ``worker_session`` is the only module that translates it to the pinned SDK.
    """

    turn_detection: Literal["stt"] = "stt"
    endpointing_mode: Literal["fixed"] = "fixed"
    min_endpointing_delay_seconds: float = 0.0
    max_endpointing_delay_seconds: float = 0.0
    interruption_mode: Literal["hard"] = "hard"
    resume_false_interruption: bool = False
    preemptive_generation: bool = False
    aec_warmup_duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.turn_detection != "stt":
            raise ValueError("voice session turn_detection must be stt")
        if self.endpointing_mode != "fixed":
            raise ValueError("voice session endpointing_mode must be fixed")
        if self.interruption_mode != "hard":
            raise ValueError("voice session interruption_mode must be hard")
        for name in (
            "min_endpointing_delay_seconds",
            "max_endpointing_delay_seconds",
            "aec_warmup_duration_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"voice session {name} must be a finite non-negative number")
        if self.max_endpointing_delay_seconds < self.min_endpointing_delay_seconds:
            raise ValueError("voice session maximum endpointing delay cannot be below minimum")
        for name in ("resume_false_interruption", "preemptive_generation"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"voice session {name} must be a boolean")


@dataclass(frozen=True)
class VoiceMediaPolicy:
    """Provider-neutral room media shape for one deterministic RTC profile."""

    input_sample_rate: int = 16_000
    input_channels: int = 1
    input_frame_size_ms: int = 20
    input_noise_cancellation: bool = False
    input_auto_gain_control: bool = False
    input_preconnect: bool = True
    output_sample_rate: int = 24_000
    output_channels: int = 1
    output_track_source: Literal["microphone"] = "microphone"
    output_track_name: str = "murmur_voice_v2_audio"
    text_input: bool = False
    text_output: bool = False

    def __post_init__(self) -> None:
        for name in (
            "input_sample_rate",
            "input_channels",
            "input_frame_size_ms",
            "output_sample_rate",
            "output_channels",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"voice media {name} must be a positive integer")
        for name in (
            "input_noise_cancellation",
            "input_auto_gain_control",
            "input_preconnect",
            "text_input",
            "text_output",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"voice media {name} must be a boolean")
        if self.input_noise_cancellation:
            raise ValueError("voice media noise cancellation requires an explicit processor")
        if self.output_track_source != "microphone":
            raise ValueError("voice media output_track_source must be microphone")
        if not is_contract_id(self.output_track_name):
            raise ValueError("voice media output_track_name must be a contract identifier")


@dataclass(frozen=True)
class PreparedVoiceProfile:
    """Explicit objects used to construct one LiveKit ``AgentSession``.

    String model identifiers are intentionally rejected by the worker.  They are
    LiveKit managed-inference selectors, whereas Murmur's selected architecture
    calls direct provider objects supplied by an installed adapter.
    """

    profile_id: str
    instructions: str
    stt: object
    llm: object
    tts: object
    vad: object | None = None
    close_callback: Callable[[], Awaitable[None]] | None = None
    session_policy: VoiceSessionPolicy | None = None
    media_policy: VoiceMediaPolicy | None = None
    readiness: ProfileReadiness | None = None
    connection_policy: VoiceConnectionPolicy | None = None

    def __post_init__(self) -> None:
        if self.session_policy is not None and not isinstance(
            self.session_policy, VoiceSessionPolicy
        ):
            raise ValueError("prepared voice session_policy is invalid")
        if self.media_policy is not None and not isinstance(self.media_policy, VoiceMediaPolicy):
            raise ValueError("prepared voice media_policy is invalid")
        if self.readiness is not None:
            if not isinstance(self.readiness, ProfileReadiness):
                raise ValueError("prepared voice readiness is invalid")
            if self.readiness.profile_id != self.profile_id:
                raise ValueError("prepared voice readiness profile ID does not match")
        if self.connection_policy is not None and not isinstance(
            self.connection_policy, VoiceConnectionPolicy
        ):
            raise ValueError("prepared voice connection_policy is invalid")


class VoiceProfileProvider(Protocol):
    """One direct-provider implementation selected by a server profile ID."""

    async def admit(self, scope: VoiceProfileScope) -> ProfileAdmission: ...

    async def prepare(self, scope: VoiceProfileScope) -> PreparedVoiceProfile: ...


class VoiceProfileRegistry:
    """Fail-closed registry for server-selected Voice V2 profiles."""

    def __init__(self, providers: Mapping[str, VoiceProfileProvider]) -> None:
        if not providers:
            raise ValueError("at least one Voice V2 profile provider is required")
        if any(not profile_id.strip() for profile_id in providers):
            raise ValueError("Voice V2 profile IDs must not be empty")
        self._providers = dict(providers)

    async def admit(self, scope: VoiceProfileScope) -> ProfileAdmission:
        provider = self._provider(scope.profile_id)
        result = await provider.admit(scope)
        if result.profile_id != scope.profile_id:
            raise VoiceProfileUnavailable("profile admission returned a different profile ID")
        return result

    async def prepare(
        self, scope: VoiceProfileScope
    ) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
        provider = self._provider(scope.profile_id)
        prepared = await provider.prepare(scope)
        if prepared.profile_id != scope.profile_id:
            await _close_prepared(prepared)
            raise VoiceProfileUnavailable("prepared profile returned a different profile ID")
        if not prepared.instructions.strip():
            await _close_prepared(prepared)
            raise VoiceProfileUnavailable("prepared profile instructions are empty")
        readiness = prepared.readiness
        if readiness is None:
            await _close_prepared(prepared)
            raise VoiceProfileUnavailable("prepared profile omitted readiness evidence")
        if readiness.profile_id != scope.profile_id:
            await _close_prepared(prepared)
            raise VoiceProfileUnavailable("profile readiness returned a different profile ID")
        return readiness, prepared

    def _provider(self, profile_id: str) -> VoiceProfileProvider:
        provider = self._providers.get(profile_id)
        if provider is None:
            raise VoiceProfileUnavailable(f"unsupported Voice V2 profile: {profile_id}")
        return provider


class UnavailableVoiceProfileProvider:
    """Honest default until direct STT/LLM/TTS adapters are installed and wired."""

    def __init__(self, profile_id: str, reason: str) -> None:
        self._profile_id = profile_id
        self._reason = reason

    async def admit(self, scope: VoiceProfileScope) -> ProfileAdmission:
        del scope
        raise VoiceProfileUnavailable(self._reason)

    async def prepare(self, scope: VoiceProfileScope) -> PreparedVoiceProfile:
        del scope
        raise VoiceProfileUnavailable(self._reason)


class DeterministicVoiceProfileProvider:
    """Injectable, provider/network-free profile implementation for tests.

    The opaque component objects are consumed only by an injected session factory;
    they are not presented as real media providers.  RTC end-to-end tests can
    replace them with proper local provider adapters through the same protocol.
    """

    def __init__(
        self,
        profile_id: str,
        *,
        components: Sequence[str] = ("stt", "llm", "tts"),
        fail_preflight: str | None = None,
        stt: object | None = None,
        llm: object | None = None,
        tts: object | None = None,
        vad: object | None = None,
        close_callback: Callable[[], Awaitable[None]] | None = None,
        session_policy: VoiceSessionPolicy | None = None,
        media_policy: VoiceMediaPolicy | None = None,
        config_hash: str | None = None,
        provider_models: Sequence[ProviderModelReadiness] = (),
        connection_policy: VoiceConnectionPolicy | None = None,
    ) -> None:
        self._profile_id = profile_id
        self._components = tuple(components)
        self._fail_preflight = fail_preflight
        self._stt = stt if stt is not None else object()
        self._llm = llm if llm is not None else object()
        self._tts = tts if tts is not None else object()
        self._vad = vad
        self._close_callback = close_callback
        self._session_policy = session_policy
        self._media_policy = media_policy
        self._config_hash = config_hash or _legacy_config_hash(profile_id)
        self._provider_models = tuple(provider_models)
        self._connection_policy = connection_policy
        self.admission_calls = 0
        self.prepare_calls = 0

    @property
    def preflight_calls(self) -> int:
        """Backward-compatible counter name for cheap admission calls."""
        return self.admission_calls

    async def admit(self, scope: VoiceProfileScope) -> ProfileAdmission:
        self.admission_calls += 1
        if scope.profile_id != self._profile_id:
            raise VoiceProfileUnavailable("deterministic profile scope mismatch")
        if self._fail_preflight is not None:
            raise VoiceProfileUnavailable(self._fail_preflight)
        return ProfileAdmission(
            profile_id=self._profile_id,
            required_components=self._components,
            config_hash=self._config_hash,
        )

    async def prepare(self, scope: VoiceProfileScope) -> PreparedVoiceProfile:
        self.prepare_calls += 1
        if scope.profile_id != self._profile_id:
            raise VoiceProfileUnavailable("deterministic profile scope mismatch")
        readiness = ProfileReadiness(
            profile_id=self._profile_id,
            required_components=self._components,
            ready_components=self._components,
            config_hash=self._config_hash,
            provider_models=self._provider_models,
        )
        return PreparedVoiceProfile(
            profile_id=self._profile_id,
            instructions=scope.system_prompt,
            stt=self._stt,
            llm=self._llm,
            tts=self._tts,
            vad=self._vad,
            close_callback=self._close_callback,
            session_policy=self._session_policy,
            media_policy=self._media_policy,
            readiness=readiness,
            connection_policy=self._connection_policy,
        )


def _validated_components(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        raise ValueError(f"{name} must not be empty")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{name} must contain non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique values")
    return values


async def _close_prepared(prepared: PreparedVoiceProfile) -> None:
    if prepared.close_callback is not None:
        await prepared.close_callback()


def _validated_config_hash(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("profile config_hash must be a lowercase SHA-256 digest")
    return value


def _legacy_config_hash(profile_id: str) -> str:
    return sha256(f"murmur-legacy-profile:{profile_id}".encode()).hexdigest()

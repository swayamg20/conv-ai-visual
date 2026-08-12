"""Provider-neutral profile seam for the self-hosted Voice V2 worker.

This module deliberately does not import LiveKit or provider plugins.  A profile
factory proves that its explicit provider objects are usable, then hands those
objects to the worker.  The worker remains the sole owner of ``AgentSession``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


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
class ProfilePreflight:
    """Components a provider factory has actually checked as usable."""

    profile_id: str
    required_components: tuple[str, ...]
    ready_components: tuple[str, ...]

    def __post_init__(self) -> None:
        required = _validated_components("required_components", self.required_components)
        ready = _validated_components("ready_components", self.ready_components)
        if not set(required).issubset(ready):
            raise ValueError("profile preflight did not ready every required component")


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


class VoiceProfileProvider(Protocol):
    """One direct-provider implementation selected by a server profile ID."""

    async def preflight(self, scope: VoiceProfileScope) -> ProfilePreflight: ...

    async def prepare(self, scope: VoiceProfileScope) -> PreparedVoiceProfile: ...


class VoiceProfileRegistry:
    """Fail-closed registry for server-selected Voice V2 profiles."""

    def __init__(self, providers: Mapping[str, VoiceProfileProvider]) -> None:
        if not providers:
            raise ValueError("at least one Voice V2 profile provider is required")
        if any(not profile_id.strip() for profile_id in providers):
            raise ValueError("Voice V2 profile IDs must not be empty")
        self._providers = dict(providers)

    async def preflight(self, scope: VoiceProfileScope) -> ProfilePreflight:
        provider = self._provider(scope.profile_id)
        result = await provider.preflight(scope)
        if result.profile_id != scope.profile_id:
            raise VoiceProfileUnavailable("profile preflight returned a different profile ID")
        return result

    async def prepare(
        self, scope: VoiceProfileScope
    ) -> tuple[ProfilePreflight, PreparedVoiceProfile]:
        # Preflight is deliberately explicit and precedes construction.  A provider
        # that cannot prove readiness never creates a session or publishes Ready.
        preflight = await self.preflight(scope)
        prepared = await self._provider(scope.profile_id).prepare(scope)
        if prepared.profile_id != scope.profile_id:
            await _close_prepared(prepared)
            raise VoiceProfileUnavailable("prepared profile returned a different profile ID")
        if not prepared.instructions.strip():
            await _close_prepared(prepared)
            raise VoiceProfileUnavailable("prepared profile instructions are empty")
        return preflight, prepared

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

    async def preflight(self, scope: VoiceProfileScope) -> ProfilePreflight:
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
    ) -> None:
        self._profile_id = profile_id
        self._components = tuple(components)
        self._fail_preflight = fail_preflight
        self._stt = stt if stt is not None else object()
        self._llm = llm if llm is not None else object()
        self._tts = tts if tts is not None else object()
        self._vad = vad
        self._close_callback = close_callback
        self.preflight_calls = 0
        self.prepare_calls = 0

    async def preflight(self, scope: VoiceProfileScope) -> ProfilePreflight:
        self.preflight_calls += 1
        if scope.profile_id != self._profile_id:
            raise VoiceProfileUnavailable("deterministic profile scope mismatch")
        if self._fail_preflight is not None:
            raise VoiceProfileUnavailable(self._fail_preflight)
        return ProfilePreflight(
            profile_id=self._profile_id,
            required_components=self._components,
            ready_components=self._components,
        )

    async def prepare(self, scope: VoiceProfileScope) -> PreparedVoiceProfile:
        self.prepare_calls += 1
        if scope.profile_id != self._profile_id:
            raise VoiceProfileUnavailable("deterministic profile scope mismatch")
        return PreparedVoiceProfile(
            profile_id=self._profile_id,
            instructions=scope.system_prompt,
            stt=self._stt,
            llm=self._llm,
            tts=self._tts,
            vad=self._vad,
            close_callback=self._close_callback,
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

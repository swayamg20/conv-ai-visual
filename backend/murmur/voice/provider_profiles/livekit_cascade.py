"""Direct Deepgram/Groq/ElevenLabs profile for LiveKit Agents 1.6.9."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from murmur.voice.profile import (
    PreparedVoiceProfile,
    ProfileAdmission,
    ProfileReadiness,
    ProviderModelReadiness,
    VoiceConnectionPolicy,
    VoiceMediaPolicy,
    VoiceProfileScope,
    VoiceProfileUnavailable,
    VoiceSessionPolicy,
)
from murmur.voice.provider_probe import (
    HttpxProviderProbeTransport,
    MetadataProbeEvidence,
    ProviderProbeError,
    ProviderProbeTransport,
    visible_string_ids,
)

DIRECT_CASCADE_PROFILE_ID = "livekit-agents-cascade-v1"
REQUIRED_COMPONENTS = ("stt", "llm", "tts")

_DEEPGRAM_MODELS_URL = "https://api.deepgram.com/v1/models"
_DEEPGRAM_AUTH_URL = "https://api.deepgram.com/v1/auth/token"
_GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
_ELEVENLABS_MODELS_URL = "https://api.elevenlabs.io/v1/models"
_ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"

_EXACT_DEEPGRAM_MODEL = "nova-3"
_EXACT_DEEPGRAM_LANGUAGE = "multi"
_EXACT_DEEPGRAM_SAMPLE_RATE_HZ = 16_000
_EXACT_DEEPGRAM_ENDPOINTING_MS = 300
_EXACT_DEEPGRAM_UTTERANCE_END_MS = 1_000
_EXACT_GROQ_MODEL = "openai/gpt-oss-120b"
_EXACT_ELEVENLABS_MODEL = "eleven_flash_v2_5"
_EXACT_ELEVENLABS_ENCODING = "pcm_24000"

_PLACEHOLDER_VALUES = frozenset(
    {
        "changeme",
        "change-me",
        "dummy",
        "example",
        "fake",
        "placeholder",
        "replace-me",
        "your-api-key",
        "your_api_key",
    }
)


@dataclass(frozen=True, kw_only=True)
class DirectCascadeSettings:
    """Complete, non-global configuration for the named deterministic profile."""

    profile_id: str
    deepgram_api_key: str
    groq_api_key: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    deepgram_model: str = _EXACT_DEEPGRAM_MODEL
    deepgram_language: str = _EXACT_DEEPGRAM_LANGUAGE
    deepgram_sample_rate_hz: int = _EXACT_DEEPGRAM_SAMPLE_RATE_HZ
    deepgram_endpointing_ms: int = _EXACT_DEEPGRAM_ENDPOINTING_MS
    deepgram_utterance_end_ms: int = _EXACT_DEEPGRAM_UTTERANCE_END_MS
    groq_model: str = _EXACT_GROQ_MODEL
    elevenlabs_model: str = _EXACT_ELEVENLABS_MODEL
    elevenlabs_encoding: str = _EXACT_ELEVENLABS_ENCODING
    probe_timeout_seconds: float = 4.0

    def validate(self) -> None:
        expected = {
            "profile_id": DIRECT_CASCADE_PROFILE_ID,
            "deepgram_model": _EXACT_DEEPGRAM_MODEL,
            "deepgram_language": _EXACT_DEEPGRAM_LANGUAGE,
            "deepgram_sample_rate_hz": _EXACT_DEEPGRAM_SAMPLE_RATE_HZ,
            "deepgram_endpointing_ms": _EXACT_DEEPGRAM_ENDPOINTING_MS,
            "deepgram_utterance_end_ms": _EXACT_DEEPGRAM_UTTERANCE_END_MS,
            "groq_model": _EXACT_GROQ_MODEL,
            "elevenlabs_model": _EXACT_ELEVENLABS_MODEL,
            "elevenlabs_encoding": _EXACT_ELEVENLABS_ENCODING,
        }
        for name, expected_value in expected.items():
            if getattr(self, name) != expected_value:
                raise VoiceProfileUnavailable(
                    f"{DIRECT_CASCADE_PROFILE_ID} requires {name}={expected_value!r}"
                )
        for name in (
            "deepgram_api_key",
            "groq_api_key",
            "elevenlabs_api_key",
            "elevenlabs_voice_id",
        ):
            _require_real_secret_or_id(name, getattr(self, name))
        timeout = self.probe_timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 15
        ):
            raise VoiceProfileUnavailable(
                "direct provider probe timeout must be between zero and 15 seconds"
            )

    def config_hash(self) -> str:
        """Hash only non-secret behavior; credentials never enter wire metadata."""

        self.validate()
        document = {
            "profile_id": self.profile_id,
            "deepgram_model": self.deepgram_model,
            "deepgram_language": self.deepgram_language,
            "deepgram_sample_rate_hz": self.deepgram_sample_rate_hz,
            "deepgram_endpointing_ms": self.deepgram_endpointing_ms,
            "deepgram_utterance_end_ms": self.deepgram_utterance_end_ms,
            "groq_model": self.groq_model,
            "elevenlabs_model": self.elevenlabs_model,
            "elevenlabs_encoding": self.elevenlabs_encoding,
            "elevenlabs_voice_id": self.elevenlabs_voice_id,
        }
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class DirectComponentFactories(Protocol):
    """Optional-import boundary for the pinned LiveKit provider plugins."""

    def make_stt(self, settings: DirectCascadeSettings) -> object: ...

    def make_llm(self, settings: DirectCascadeSettings) -> object: ...

    def make_tts(self, settings: DirectCascadeSettings) -> object: ...


class LiveKitPluginFactories:
    """Construct exact direct-provider objects without managed inference."""

    def _plugins(self) -> tuple[Any, Any, Any]:
        try:
            from livekit.plugins import deepgram, elevenlabs, groq
        except ImportError as exc:
            raise VoiceProfileUnavailable(
                "Voice V2 direct-provider plugins are not installed; install the voice-v2 extra"
            ) from exc
        return deepgram, groq, elevenlabs

    def make_stt(self, settings: DirectCascadeSettings) -> object:
        deepgram, _, _ = self._plugins()
        return deepgram.STT(
            api_key=settings.deepgram_api_key,
            model=settings.deepgram_model,
            language=settings.deepgram_language,
            sample_rate=settings.deepgram_sample_rate_hz,
            endpointing_ms=settings.deepgram_endpointing_ms,
            utterance_end_ms=settings.deepgram_utterance_end_ms,
            interim_results=True,
            punctuate=True,
            smart_format=True,
            vad_events=True,
        )

    def make_llm(self, settings: DirectCascadeSettings) -> object:
        _, groq, _ = self._plugins()
        return groq.LLM(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            max_retries=0,
        )

    def make_tts(self, settings: DirectCascadeSettings) -> object:
        _, _, elevenlabs = self._plugins()
        return elevenlabs.TTS(
            api_key=settings.elevenlabs_api_key,
            voice_id=settings.elevenlabs_voice_id,
            model=settings.elevenlabs_model,
            encoding=settings.elevenlabs_encoding,
            auto_mode=True,
        )


class LiveKitDirectCascadeProvider:
    """Provider with static admission and one authoritative prepare probe."""

    def __init__(
        self,
        settings: DirectCascadeSettings,
        *,
        factories: DirectComponentFactories | None = None,
        probe_transport: ProviderProbeTransport | None = None,
    ) -> None:
        self._settings = settings
        self._factories = factories or LiveKitPluginFactories()
        self._probe_transport = probe_transport or HttpxProviderProbeTransport()

    async def admit(self, scope: VoiceProfileScope) -> ProfileAdmission:
        self._validate_scope(scope)
        self._settings.validate()
        _validate_factory_surface(self._factories)
        return ProfileAdmission(
            profile_id=self._settings.profile_id,
            required_components=REQUIRED_COMPONENTS,
            config_hash=self._settings.config_hash(),
        )

    async def prepare(self, scope: VoiceProfileScope) -> PreparedVoiceProfile:
        admission = await self.admit(scope)
        evidence = await self._probe_all()
        constructed: list[object] = []
        close_once = _IdempotentComponentCloser(constructed)
        try:
            stt = self._factories.make_stt(self._settings)
            constructed.append(stt)
            llm = self._factories.make_llm(self._settings)
            constructed.append(llm)
            tts = self._factories.make_tts(self._settings)
            constructed.append(tts)
        except asyncio.CancelledError:
            try:
                await close_once()
            except Exception:
                # Cancellation is authoritative; an ordinary cleanup error must
                # not replace it while unwinding partially constructed objects.
                pass
            raise
        except Exception:
            try:
                await close_once()
            except Exception:
                # Preserve the construction failure. Cleanup is best effort only
                # because there is no prepared owner to report a second error.
                pass
            raise

        readiness = ProfileReadiness(
            profile_id=self._settings.profile_id,
            required_components=REQUIRED_COMPONENTS,
            ready_components=REQUIRED_COMPONENTS,
            config_hash=admission.config_hash,
            provider_models=tuple(
                ProviderModelReadiness(
                    component=item.component,
                    provider=item.provider,
                    model=item.selected_resource,
                )
                for item in evidence
            ),
            limitations=tuple(
                dict.fromkeys(limitation for item in evidence for limitation in item.limitations)
            ),
        )
        return PreparedVoiceProfile(
            profile_id=self._settings.profile_id,
            instructions=scope.system_prompt,
            stt=stt,
            llm=llm,
            tts=tts,
            close_callback=close_once,
            readiness=readiness,
            session_policy=VoiceSessionPolicy(
                turn_detection="stt",
                endpointing_mode="fixed",
                min_endpointing_delay_seconds=0.0,
                max_endpointing_delay_seconds=0.0,
                interruption_mode="hard",
                resume_false_interruption=False,
                preemptive_generation=False,
                aec_warmup_duration_seconds=0.0,
            ),
            media_policy=VoiceMediaPolicy(
                input_sample_rate=self._settings.deepgram_sample_rate_hz,
                input_channels=1,
                input_frame_size_ms=20,
                input_noise_cancellation=False,
                input_auto_gain_control=False,
                input_preconnect=True,
                output_sample_rate=24_000,
                output_channels=1,
                output_track_source="microphone",
                output_track_name="murmur_voice_v2_audio",
                text_input=False,
                text_output=False,
            ),
            connection_policy=VoiceConnectionPolicy(),
        )

    def _validate_scope(self, scope: VoiceProfileScope) -> None:
        if scope.profile_id != self._settings.profile_id:
            raise VoiceProfileUnavailable("direct cascade profile scope mismatch")
        if not scope.system_prompt.strip():
            raise VoiceProfileUnavailable("direct cascade system prompt is empty")

    async def _probe_all(self) -> tuple[MetadataProbeEvidence, ...]:
        try:
            async with asyncio.timeout(self._settings.probe_timeout_seconds):
                async with asyncio.TaskGroup() as group:
                    deepgram_auth = group.create_task(self._probe_deepgram_auth())
                    deepgram_models = group.create_task(self._probe_deepgram_models())
                    groq = group.create_task(self._probe_groq())
                    elevenlabs_models = group.create_task(self._probe_elevenlabs_models())
                    elevenlabs_voices = group.create_task(self._probe_elevenlabs_voices())
        except TimeoutError as exc:
            raise VoiceProfileUnavailable("direct provider metadata probes timed out") from exc
        except ExceptionGroup as exc:
            cause = _first_leaf_exception(exc)
            if isinstance(cause, ProviderProbeError):
                raise VoiceProfileUnavailable(
                    "direct provider metadata readiness failed"
                ) from cause
            raise VoiceProfileUnavailable("direct provider metadata probes failed") from cause
        except Exception as exc:
            raise VoiceProfileUnavailable("direct provider metadata probes failed") from exc

        # TaskGroup has awaited every key-bearing request before any result is
        # consumed. It also cancels and awaits every sibling on failure/timeout.
        deepgram_auth.result()
        deepgram_resources = deepgram_models.result()
        elevenlabs_model_ids = elevenlabs_models.result()
        elevenlabs_voice_ids = elevenlabs_voices.result()
        return (
            MetadataProbeEvidence(
                component="stt",
                provider="deepgram",
                selected_resource=self._settings.deepgram_model,
                observed_resources=deepgram_resources,
                proves=(
                    "Deepgram accepted the API key at the auth-token endpoint",
                    "Deepgram public model catalog was reachable",
                    "selected STT model was visible",
                ),
                limitations=(
                    "STT streaming, language accuracy, endpointing, quota, and latency remain unproven",
                ),
            ),
            groq.result(),
            MetadataProbeEvidence(
                component="tts",
                provider="elevenlabs",
                selected_resource=self._settings.elevenlabs_model,
                observed_resources=(*elevenlabs_model_ids, *elevenlabs_voice_ids),
                proves=(
                    "ElevenLabs accepted model and voice requests",
                    "selected TTS model and voice were visible",
                ),
                limitations=(
                    "TTS streaming, PCM encoding, pronunciation, quota, and latency remain unproven",
                ),
            ),
        )

    async def _probe_deepgram_auth(self) -> None:
        headers = {"Authorization": f"Token {self._settings.deepgram_api_key}"}
        auth = await self._probe_transport.get_json(
            _DEEPGRAM_AUTH_URL,
            headers=headers,
            timeout_seconds=self._settings.probe_timeout_seconds,
        )
        # HTTP 2xx is the authentication proof. The documented response contains
        # key details, not a guaranteed token field; never retain those details.
        if not isinstance(auth, Mapping) or not auth:
            raise ProviderProbeError("Deepgram auth response did not contain key details")

    async def _probe_deepgram_models(self) -> tuple[str, ...]:
        payload = await self._probe_transport.get_json(
            _DEEPGRAM_MODELS_URL,
            headers={"Authorization": f"Token {self._settings.deepgram_api_key}"},
            timeout_seconds=self._settings.probe_timeout_seconds,
        )
        if not isinstance(payload, Mapping):
            raise ProviderProbeError("Deepgram model response is not a JSON object")
        resources = visible_string_ids(
            payload,
            collection_key="stt",
            id_keys=("canonical_name", "name"),
        )
        if not any(
            identifier == self._settings.deepgram_model
            or identifier.startswith(f"{self._settings.deepgram_model}-")
            for identifier in resources
        ):
            raise ProviderProbeError("selected Deepgram STT model is not visible")
        return resources

    async def _probe_groq(self) -> MetadataProbeEvidence:
        payload = await self._probe_transport.get_json(
            _GROQ_MODELS_URL,
            headers={"Authorization": f"Bearer {self._settings.groq_api_key}"},
            timeout_seconds=self._settings.probe_timeout_seconds,
        )
        if not isinstance(payload, Mapping):
            raise ProviderProbeError("Groq model response is not a JSON object")
        resources = visible_string_ids(payload, collection_key="data")
        if self._settings.groq_model not in resources:
            raise ProviderProbeError("selected Groq LLM model is not visible")
        return MetadataProbeEvidence(
            component="llm",
            provider="groq",
            selected_resource=self._settings.groq_model,
            observed_resources=resources,
            proves=("Groq accepted the models request", "selected LLM model was visible"),
            limitations=(
                "LLM token generation, tool behavior, quota, and latency remain unproven",
            ),
        )

    async def _probe_elevenlabs_models(self) -> tuple[str, ...]:
        models = await self._probe_transport.get_json(
            _ELEVENLABS_MODELS_URL,
            headers={"xi-api-key": self._settings.elevenlabs_api_key},
            timeout_seconds=self._settings.probe_timeout_seconds,
        )
        model_ids = _visible_elevenlabs_models(models)
        if self._settings.elevenlabs_model not in model_ids:
            raise ProviderProbeError("selected ElevenLabs TTS model is not visible")
        return model_ids

    async def _probe_elevenlabs_voices(self) -> tuple[str, ...]:
        voices = await self._probe_transport.get_json(
            _ELEVENLABS_VOICES_URL,
            headers={"xi-api-key": self._settings.elevenlabs_api_key},
            timeout_seconds=self._settings.probe_timeout_seconds,
        )
        if not isinstance(voices, Mapping):
            raise ProviderProbeError("ElevenLabs voice response is not a JSON object")
        voice_ids = visible_string_ids(voices, collection_key="voices", id_keys=("voice_id",))
        if self._settings.elevenlabs_voice_id not in voice_ids:
            raise ProviderProbeError("selected ElevenLabs voice is not visible")
        return voice_ids


def build_direct_cascade_provider(
    settings: DirectCascadeSettings,
    *,
    factories: DirectComponentFactories | None = None,
    probe_transport: ProviderProbeTransport | None = None,
) -> LiveKitDirectCascadeProvider:
    """Build the exact named provider without importing application globals."""

    settings.validate()
    return LiveKitDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=probe_transport,
    )


def build_direct_cascade_provider_from_config(config: object) -> LiveKitDirectCascadeProvider:
    """Thin composition adapter for the existing class-backed application config."""

    try:
        timeout = float(config.VOICE_V2_PROVIDER_PROBE_TIMEOUT_SECONDS)  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError) as exc:
        raise VoiceProfileUnavailable("Voice V2 provider probe timeout is invalid") from exc
    return build_direct_cascade_provider(
        DirectCascadeSettings(
            profile_id=str(getattr(config, "VOICE_V2_PROFILE_ID", "") or "").strip(),
            deepgram_api_key=str(getattr(config, "DEEPGRAM_KEY", "") or "").strip(),
            groq_api_key=str(getattr(config, "GROQ_API_KEY", "") or "").strip(),
            elevenlabs_api_key=str(getattr(config, "ELEVENLABS_API_KEY", "") or "").strip(),
            elevenlabs_voice_id=str(getattr(config, "ELEVENLABS_VOICE_ID", "") or "").strip(),
            probe_timeout_seconds=timeout,
        )
    )


class _IdempotentComponentCloser:
    def __init__(self, components: list[object]) -> None:
        self._components = components
        self._lock = asyncio.Lock()
        self._completed: set[int] = set()

    async def __call__(self) -> None:
        async with self._lock:
            if len(self._completed) == len(self._components):
                return
            errors: list[Exception] = []
            for index in reversed(range(len(self._components))):
                if index in self._completed:
                    continue
                component = self._components[index]
                close = getattr(component, "aclose", None)
                if not callable(close):
                    self._completed.add(index)
                    continue
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    errors.append(exc)
                else:
                    # Mark ownership released only after close actually returns.
                    # Cancellation therefore leaves this exact component retryable.
                    self._completed.add(index)
            if errors:
                raise VoiceProfileUnavailable("direct provider cleanup failed") from errors[0]


def _first_leaf_exception(group: ExceptionGroup) -> Exception:
    for error in group.exceptions:
        if isinstance(error, ExceptionGroup):
            return _first_leaf_exception(error)
        return error
    return group


def _visible_elevenlabs_models(payload: object) -> tuple[str, ...]:
    if isinstance(payload, list):
        return visible_string_ids(
            {"models": payload},
            collection_key="models",
            id_keys=("model_id",),
        )
    if not isinstance(payload, Mapping):
        raise ProviderProbeError("ElevenLabs model response is not a JSON object or array")
    for key in ("models", "data"):
        if key in payload:
            return visible_string_ids(payload, collection_key=key, id_keys=("model_id", "id"))
    raise ProviderProbeError("ElevenLabs model response has no model collection")


def _validate_factory_surface(factories: DirectComponentFactories) -> None:
    for name in ("make_stt", "make_llm", "make_tts"):
        if not callable(getattr(factories, name, None)):
            raise VoiceProfileUnavailable(f"direct component factory is missing {name}")
    if isinstance(factories, LiveKitPluginFactories):
        factories._plugins()


def _require_real_secret_or_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VoiceProfileUnavailable(f"{name} is required for {DIRECT_CASCADE_PROFILE_ID}")
    cleaned = value.strip()
    normalized = cleaned.lower().strip("<>[]{}() ")
    if (
        normalized in _PLACEHOLDER_VALUES
        or normalized.startswith(("your-", "your_", "example-", "example_", "test-", "test_"))
        or "placeholder" in normalized
    ):
        raise VoiceProfileUnavailable(f"{name} is a placeholder")
    return cleaned

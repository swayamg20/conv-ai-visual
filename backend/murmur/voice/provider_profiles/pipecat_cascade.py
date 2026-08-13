"""Direct Deepgram/Groq/ElevenLabs profile for Pipecat 1.7.

The profile mirrors the LiveKit challenger manifest, but constructs Pipecat
services independently.  Static admission never contacts a provider or builds
a service.  ``prepare`` is the sole authoritative metadata preflight.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from murmur.voice.profile import (
    ProfileAdmission,
    ProfileReadiness,
    ProviderModelReadiness,
    VoiceAPIConnectionPolicy,
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

PIPECAT_DIRECT_CASCADE_PROFILE_ID = "pipecat-direct-cascade-v1"
REQUIRED_COMPONENTS = ("stt", "llm", "tts")

_DEEPGRAM_AUTH_URL = "https://api.deepgram.com/v1/auth/token"
_DEEPGRAM_MODELS_URL = "https://api.deepgram.com/v1/models"
_GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
_ELEVENLABS_MODELS_URL = "https://api.elevenlabs.io/v1/models"
_ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"

_DEEPGRAM_MODEL = "nova-3"
_DEEPGRAM_LANGUAGE = "multi"
_DEEPGRAM_SAMPLE_RATE = 16_000
_DEEPGRAM_ENDPOINTING_MS = 300
_DEEPGRAM_UTTERANCE_END_MS = 1_000
_GROQ_MODEL = "openai/gpt-oss-120b"
_ELEVENLABS_MODEL = "eleven_flash_v2_5"
_ELEVENLABS_SAMPLE_RATE = 24_000
_DEFAULT_PROVIDER_CLEANUP_TIMEOUT_SECONDS = 5.0
_MAX_PROVIDER_CLEANUP_TIMEOUT_SECONDS = 15.0
_MAX_GROQ_STREAM_CLEANUP_RESERVE_SECONDS = 0.25
_PLACEHOLDERS = frozenset(
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
class PipecatCascadeSettings:
    """Complete non-global configuration for the pinned Pipecat profile."""

    profile_id: str
    deepgram_api_key: str = field(repr=False)
    groq_api_key: str = field(repr=False)
    elevenlabs_api_key: str = field(repr=False)
    elevenlabs_voice_id: str
    deepgram_model: str = _DEEPGRAM_MODEL
    deepgram_language: str = _DEEPGRAM_LANGUAGE
    deepgram_sample_rate_hz: int = _DEEPGRAM_SAMPLE_RATE
    deepgram_endpointing_ms: int = _DEEPGRAM_ENDPOINTING_MS
    deepgram_utterance_end_ms: int = _DEEPGRAM_UTTERANCE_END_MS
    groq_model: str = _GROQ_MODEL
    elevenlabs_model: str = _ELEVENLABS_MODEL
    elevenlabs_sample_rate_hz: int = _ELEVENLABS_SAMPLE_RATE
    probe_timeout_seconds: float = 4.0

    def validate(self) -> None:
        expected: Mapping[str, object] = {
            "profile_id": PIPECAT_DIRECT_CASCADE_PROFILE_ID,
            "deepgram_model": _DEEPGRAM_MODEL,
            "deepgram_language": _DEEPGRAM_LANGUAGE,
            "deepgram_sample_rate_hz": _DEEPGRAM_SAMPLE_RATE,
            "deepgram_endpointing_ms": _DEEPGRAM_ENDPOINTING_MS,
            "deepgram_utterance_end_ms": _DEEPGRAM_UTTERANCE_END_MS,
            "groq_model": _GROQ_MODEL,
            "elevenlabs_model": _ELEVENLABS_MODEL,
            "elevenlabs_sample_rate_hz": _ELEVENLABS_SAMPLE_RATE,
        }
        for name, expected_value in expected.items():
            if getattr(self, name) != expected_value:
                raise VoiceProfileUnavailable(
                    f"{PIPECAT_DIRECT_CASCADE_PROFILE_ID} requires {name}={expected_value!r}"
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
                "Pipecat provider probe timeout must be between zero and 15 seconds"
            )

    def config_hash(self) -> str:
        """Hash the shared behavior manifest without credential material."""

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
            "elevenlabs_sample_rate_hz": self.elevenlabs_sample_rate_hz,
            "elevenlabs_voice_id": self.elevenlabs_voice_id,
        }
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class PipecatComponentFactories(Protocol):
    """Construction boundary for Pipecat's public direct-service APIs."""

    def validate_available(self) -> None: ...

    def make_stt(self, settings: PipecatCascadeSettings) -> object: ...

    def make_llm(self, settings: PipecatCascadeSettings) -> object: ...

    def make_tts(self, settings: PipecatCascadeSettings) -> object: ...


class PipecatServiceFactories:
    """Construct the pinned direct services without importing application globals."""

    def _services(self) -> tuple[Any, Any, Any]:
        try:
            from pipecat.services.deepgram.stt import DeepgramSTTService
            from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
            from pipecat.services.groq.llm import GroqLLMService
        except ImportError as exc:
            raise VoiceProfileUnavailable(
                "Pipecat direct services are not installed; install the voice-pipecat extra"
            ) from exc
        return DeepgramSTTService, GroqLLMService, ElevenLabsTTSService

    def validate_available(self) -> None:
        self._services()

    def make_stt(self, settings: PipecatCascadeSettings) -> object:
        deepgram, _, _ = self._services()
        policy = _pipecat_connection_policy().stt
        service_type = _deepgram_stream_readiness_adapter(
            deepgram,
            timeout_seconds=policy.timeout_seconds,
        )
        return service_type(
            api_key=settings.deepgram_api_key,
            sample_rate=settings.deepgram_sample_rate_hz,
            settings=deepgram.Settings(
                model=settings.deepgram_model,
                language=settings.deepgram_language,
                endpointing=settings.deepgram_endpointing_ms,
                utterance_end_ms=settings.deepgram_utterance_end_ms,
                interim_results=True,
                punctuate=True,
                smart_format=True,
            ),
        )

    def make_llm(self, settings: PipecatCascadeSettings) -> object:
        _, groq, _ = self._services()
        policy = _pipecat_connection_policy().llm
        service_type = _bounded_groq_adapter(groq)
        return service_type(
            api_key=settings.groq_api_key,
            retry_timeout_secs=policy.timeout_seconds,
            retry_on_timeout=policy.max_retry > 0,
            settings=groq.Settings(
                model=settings.groq_model,
            ),
        )

    def make_tts(self, settings: PipecatCascadeSettings) -> object:
        _, _, elevenlabs = self._services()
        policy = _pipecat_connection_policy().tts
        service_type = _elevenlabs_connection_timeout_adapter(
            elevenlabs,
            timeout_seconds=policy.timeout_seconds,
        )
        return service_type(
            api_key=settings.elevenlabs_api_key,
            sample_rate=settings.elevenlabs_sample_rate_hz,
            auto_mode=True,
            settings=elevenlabs.Settings(
                model=settings.elevenlabs_model,
                voice=settings.elevenlabs_voice_id,
            ),
        )


@dataclass(frozen=True)
class PreparedPipecatProfile:
    """Job-owned Pipecat services and authoritative readiness evidence."""

    profile_id: str
    instructions: str
    stt: object
    llm: object
    tts: object
    readiness: ProfileReadiness
    close_callback: Callable[[], Awaitable[None]]
    session_policy: VoiceSessionPolicy
    media_policy: VoiceMediaPolicy
    connection_policy: VoiceConnectionPolicy
    wait_streams_ready: Callable[[], Awaitable[None]]


class _PipecatStreamReadiness:
    """One-shot readiness for the two eager streaming provider connections."""

    def __init__(self, *, stt: object, tts: object) -> None:
        stt_wait = getattr(stt, "wait_stream_ready", None)
        add_tts_handler = getattr(tts, "add_event_handler", None)
        if not callable(stt_wait):
            raise VoiceProfileUnavailable("Pipecat Deepgram service omitted stream readiness")
        if not callable(add_tts_handler):
            raise VoiceProfileUnavailable("Pipecat ElevenLabs service omitted connection events")
        self._stt_wait: Callable[[], Awaitable[None]] = stt_wait
        self._tts_ready = False
        self._tts_failure: VoiceProfileUnavailable | None = None
        self._tts_changed = asyncio.Event()
        add_tts_handler("on_connected", self._on_tts_connected)
        add_tts_handler("on_connection_error", self._on_tts_connection_error)

    async def _on_tts_connected(self, _tts: object) -> None:
        self._tts_ready = True
        self._tts_changed.set()

    async def _on_tts_connection_error(self, _tts: object, error: str) -> None:
        self._tts_failure = VoiceProfileUnavailable(
            f"Pipecat ElevenLabs stream connection failed: {error}"
        )
        self._tts_changed.set()

    async def _wait_tts(self) -> None:
        while True:
            self._tts_changed.clear()
            if self._tts_failure is not None:
                raise self._tts_failure
            if self._tts_ready:
                return
            await self._tts_changed.wait()

    async def wait(self) -> None:
        tasks = (
            asyncio.create_task(self._stt_wait(), name="pipecat-deepgram-stream-readiness"),
            asyncio.create_task(self._wait_tts(), name="pipecat-elevenlabs-stream-readiness"),
        )
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise VoiceProfileUnavailable("Pipecat provider streams failed readiness") from exc
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class PipecatDirectCascadeProvider:
    """Static admission followed by bounded provider metadata preflight."""

    def __init__(
        self,
        settings: PipecatCascadeSettings,
        *,
        factories: PipecatComponentFactories | None = None,
        probe_transport: ProviderProbeTransport | None = None,
        cleanup_timeout_seconds: float = _DEFAULT_PROVIDER_CLEANUP_TIMEOUT_SECONDS,
    ) -> None:
        _validate_cleanup_timeout(cleanup_timeout_seconds)
        self._settings = settings
        self._factories = factories or PipecatServiceFactories()
        self._probe_transport = probe_transport or HttpxProviderProbeTransport()
        self._cleanup_timeout_seconds = cleanup_timeout_seconds

    async def admit(self, scope: VoiceProfileScope) -> ProfileAdmission:
        self._validate_scope(scope)
        self._settings.validate()
        _validate_factory_surface(self._factories)
        self._factories.validate_available()
        return ProfileAdmission(
            profile_id=self._settings.profile_id,
            required_components=REQUIRED_COMPONENTS,
            config_hash=self._settings.config_hash(),
        )

    async def prepare(self, scope: VoiceProfileScope) -> PreparedPipecatProfile:
        admission = await self.admit(scope)
        evidence = await self._probe_all()
        constructed: list[object] = []
        close_once = _IdempotentComponentCloser(
            constructed,
            timeout_seconds=self._cleanup_timeout_seconds,
        )
        try:
            stt = self._factories.make_stt(self._settings)
            constructed.append(stt)
            llm = self._factories.make_llm(self._settings)
            constructed.append(llm)
            tts = self._factories.make_tts(self._settings)
            constructed.append(tts)
            stream_readiness = _PipecatStreamReadiness(stt=stt, tts=tts)
        except asyncio.CancelledError as exc:
            await _close_partial_after_cancellation(close_once, exc)
            raise
        except Exception as exc:
            await _close_partial_after_failure(close_once, exc)
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
                dict.fromkeys(
                    (
                        *(limitation for item in evidence for limitation in item.limitations),
                        "Murmur waits five seconds for initial Deepgram readiness, passes a "
                        "five-second ElevenLabs websocket open timeout, and applies its configured "
                        "aggregate runtime readiness deadline (ten seconds by default)",
                        "Deepgram and ElevenLabs reconnect mechanics are SDK-defined; Murmur treats an emitted pipeline error as terminal",
                    )
                )
            ),
        )
        return PreparedPipecatProfile(
            profile_id=self._settings.profile_id,
            instructions=scope.system_prompt,
            stt=stt,
            llm=llm,
            tts=tts,
            readiness=readiness,
            close_callback=close_once,
            session_policy=VoiceSessionPolicy(),
            media_policy=VoiceMediaPolicy(
                input_sample_rate=self._settings.deepgram_sample_rate_hz,
                output_sample_rate=self._settings.elevenlabs_sample_rate_hz,
                output_track_name="murmur_voice_v2_audio",
            ),
            connection_policy=_pipecat_connection_policy(),
            wait_streams_ready=stream_readiness.wait,
        )

    def _validate_scope(self, scope: VoiceProfileScope) -> None:
        if scope.profile_id != self._settings.profile_id:
            raise VoiceProfileUnavailable("Pipecat direct cascade profile scope mismatch")
        if not scope.system_prompt.strip():
            raise VoiceProfileUnavailable("Pipecat direct cascade system prompt is empty")

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
            raise VoiceProfileUnavailable("Pipecat provider metadata probes timed out") from exc
        except ExceptionGroup as exc:
            cause = _first_leaf_exception(exc)
            message = (
                "Pipecat provider metadata readiness failed"
                if isinstance(cause, ProviderProbeError)
                else "Pipecat provider metadata probes failed"
            )
            raise VoiceProfileUnavailable(message) from cause
        except Exception as exc:
            raise VoiceProfileUnavailable("Pipecat provider metadata probes failed") from exc

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
                    "selected STT model was visible",
                ),
                limitations=(
                    "STT streaming, multilingual accuracy, endpointing, quota, and latency remain unproven",
                ),
            ),
            groq.result(),
            MetadataProbeEvidence(
                component="tts",
                provider="elevenlabs",
                selected_resource=self._settings.elevenlabs_model,
                observed_resources=(*elevenlabs_model_ids, *elevenlabs_voice_ids),
                proves=("selected TTS model and voice were visible",),
                limitations=(
                    "TTS streaming, PCM output, pronunciation, quota, and latency remain unproven",
                ),
            ),
        )

    async def _probe_deepgram_auth(self) -> None:
        payload = await self._probe_transport.get_json(
            _DEEPGRAM_AUTH_URL,
            headers={"Authorization": f"Token {self._settings.deepgram_api_key}"},
            timeout_seconds=self._settings.probe_timeout_seconds,
        )
        if not isinstance(payload, Mapping) or not payload:
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
            item == self._settings.deepgram_model
            or item.startswith(f"{self._settings.deepgram_model}-")
            for item in resources
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
                "Groq readiness remains metadata-only; LLM generation, tools, quota, and latency remain unproven",
            ),
        )

    async def _probe_elevenlabs_models(self) -> tuple[str, ...]:
        payload = await self._probe_transport.get_json(
            _ELEVENLABS_MODELS_URL,
            headers={"xi-api-key": self._settings.elevenlabs_api_key},
            timeout_seconds=self._settings.probe_timeout_seconds,
        )
        if isinstance(payload, list):
            payload = {"models": payload}
        if not isinstance(payload, Mapping):
            raise ProviderProbeError("ElevenLabs model response is invalid")
        key = "models" if "models" in payload else "data"
        resources = visible_string_ids(payload, collection_key=key, id_keys=("model_id", "id"))
        if self._settings.elevenlabs_model not in resources:
            raise ProviderProbeError("selected ElevenLabs TTS model is not visible")
        return resources

    async def _probe_elevenlabs_voices(self) -> tuple[str, ...]:
        payload = await self._probe_transport.get_json(
            _ELEVENLABS_VOICES_URL,
            headers={"xi-api-key": self._settings.elevenlabs_api_key},
            timeout_seconds=self._settings.probe_timeout_seconds,
        )
        if not isinstance(payload, Mapping):
            raise ProviderProbeError("ElevenLabs voice response is not a JSON object")
        resources = visible_string_ids(
            payload,
            collection_key="voices",
            id_keys=("voice_id",),
        )
        if self._settings.elevenlabs_voice_id not in resources:
            raise ProviderProbeError("selected ElevenLabs voice is not visible")
        return resources


def build_pipecat_cascade_provider(
    settings: PipecatCascadeSettings,
    *,
    factories: PipecatComponentFactories | None = None,
    probe_transport: ProviderProbeTransport | None = None,
    cleanup_timeout_seconds: float = _DEFAULT_PROVIDER_CLEANUP_TIMEOUT_SECONDS,
) -> PipecatDirectCascadeProvider:
    settings.validate()
    return PipecatDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=probe_transport,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
    )


def _pipecat_connection_policy() -> VoiceConnectionPolicy:
    """One-shot qualification policy; websocket retries remain SDK-defined."""

    return VoiceConnectionPolicy(
        stt=VoiceAPIConnectionPolicy(max_retry=0, timeout_seconds=5.0),
        llm=VoiceAPIConnectionPolicy(max_retry=0, timeout_seconds=8.0),
        tts=VoiceAPIConnectionPolicy(max_retry=0, timeout_seconds=5.0),
    )


class _IdempotentComponentCloser:
    def __init__(self, components: list[object], *, timeout_seconds: float) -> None:
        _validate_cleanup_timeout(timeout_seconds)
        self._components = components
        self._timeout_seconds = timeout_seconds
        self._completed: set[int] = set()
        self._inflight: dict[int, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def __call__(self) -> None:
        async with self._lock:
            errors: list[Exception] = []
            for index in reversed(range(len(self._components))):
                if index in self._completed:
                    continue
                close = getattr(self._components[index], "cleanup", None)
                if not callable(close):
                    close = getattr(self._components[index], "aclose", None)
                if not callable(close):
                    self._completed.add(index)
                    continue
                try:
                    await self._close_component(index, close)
                except Exception as exc:
                    errors.append(exc)
                else:
                    self._completed.add(index)
            if errors:
                raise VoiceProfileUnavailable("Pipecat provider cleanup failed") from errors[0]

    async def _close_component(self, index: int, close: Callable[[], object]) -> None:
        task = self._inflight.get(index)
        if task is not None and task.done():
            self._inflight.pop(index, None)
            if not task.cancelled() and task.exception() is None:
                return
            task = None
        if task is None:
            task = asyncio.create_task(
                _invoke_cleanup(close),
                name=f"pipecat-provider-cleanup:{index}",
            )
            self._inflight[index] = task
        try:
            done, _ = await asyncio.wait((task,), timeout=self._timeout_seconds)
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_background_task_result)
            raise
        if task not in done:
            task.cancel()
            # Give cancellation-cooperative provider cleanups one scheduling
            # turn to settle. A cancellation-resistant task stays tracked and
            # is waited again instead of spawning unsafe duplicate cleanup.
            await asyncio.sleep(0)
            if task.done():
                await asyncio.gather(task, return_exceptions=True)
                self._inflight.pop(index, None)
            else:
                task.add_done_callback(_consume_background_task_result)
            raise TimeoutError("Pipecat provider component cleanup timed out")
        self._inflight.pop(index, None)
        await task


async def _close_partial_after_cancellation(
    close: _IdempotentComponentCloser,
    cancellation: asyncio.CancelledError,
) -> None:
    """Preserve cancellation while making a transient partial-close failure retryable."""

    errors: list[Exception] = []
    for _attempt in range(2):
        try:
            await close()
            return
        except Exception as exc:
            errors.append(exc)
    cancellation.add_note(f"Pipecat partial provider cleanup failed twice: {errors[-1]!r}")


async def _close_partial_after_failure(
    close: _IdempotentComponentCloser,
    primary: Exception,
) -> None:
    """Retry partial construction cleanup, then surface both failures."""

    errors: list[Exception] = []
    for _attempt in range(2):
        try:
            await close()
            return
        except Exception as exc:
            errors.append(exc)
    raise VoiceProfileUnavailable(
        "Pipecat provider construction failed and partial cleanup failed"
    ) from ExceptionGroup("Pipecat provider construction and cleanup failures", [primary, *errors])


def _deepgram_stream_readiness_adapter(
    service_type: type,
    *,
    timeout_seconds: float,
) -> type:
    """Isolate the exact private readiness seam in pinned Pipecat 1.7."""

    class MurmurReadyDeepgramSTTService(service_type):  # type: ignore[misc, valid-type]
        _murmur_stream_readiness_timeout_seconds = timeout_seconds

        async def wait_stream_ready(self) -> None:
            ready = getattr(self, "_connection_ready", None)
            connection_task = getattr(self, "_connection_task", None)
            if not isinstance(ready, asyncio.Event) or not isinstance(
                connection_task, asyncio.Future
            ):
                raise VoiceProfileUnavailable("Pipecat Deepgram stream did not start")
            if ready.is_set():
                return
            if connection_task.done():
                raise VoiceProfileUnavailable("Pipecat Deepgram stream ended before becoming ready")

            ready_task = asyncio.create_task(
                ready.wait(),
                name="pipecat-deepgram-connection-ready",
            )
            try:
                async with asyncio.timeout(self._murmur_stream_readiness_timeout_seconds):
                    done, _ = await asyncio.wait(
                        (ready_task, connection_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                if ready_task in done and ready.is_set():
                    return
                raise VoiceProfileUnavailable("Pipecat Deepgram stream ended before becoming ready")
            except TimeoutError as exc:
                raise VoiceProfileUnavailable(
                    "Pipecat Deepgram stream readiness timed out"
                ) from exc
            finally:
                if not ready_task.done():
                    ready_task.cancel()
                await asyncio.gather(ready_task, return_exceptions=True)

    MurmurReadyDeepgramSTTService.__name__ = f"MurmurReady{service_type.__name__}"
    return MurmurReadyDeepgramSTTService


def _elevenlabs_connection_timeout_adapter(
    service_type: type,
    *,
    timeout_seconds: float,
) -> type:
    """Pass the declared open deadline through Pipecat's public websocket seam."""

    class MurmurBoundedElevenLabsTTSService(service_type):  # type: ignore[misc, valid-type]
        _murmur_connection_open_timeout_seconds = timeout_seconds

        async def _websocket_connect(self, uri: str, **kwargs: Any) -> object:
            kwargs.setdefault("open_timeout", self._murmur_connection_open_timeout_seconds)
            return await super()._websocket_connect(uri, **kwargs)

    MurmurBoundedElevenLabsTTSService.__name__ = f"MurmurBounded{service_type.__name__}"
    return MurmurBoundedElevenLabsTTSService


class _BoundedAsyncStream:
    """Bound one provider stream from request creation through final close."""

    def __init__(self, stream: object, *, deadline: float, iteration_deadline: float) -> None:
        iterator_factory = getattr(stream, "__aiter__", None)
        if not callable(iterator_factory):
            raise VoiceProfileUnavailable("Pipecat Groq response omitted async iteration")
        self._stream = stream
        self._iterator = iterator_factory()
        self._deadline = deadline
        self._iteration_deadline = iteration_deadline
        self._close_lock = asyncio.Lock()
        self._terminal = False
        self._expired = False
        self._cleanup_complete = False
        self._watchdog_task = asyncio.create_task(
            self._expire(),
            name="pipecat-groq-stream-deadline",
        )

    def __aiter__(self) -> _BoundedAsyncStream:
        return self

    async def __anext__(self) -> object:
        if self._terminal:
            if self._expired:
                raise TimeoutError("Pipecat Groq stream exceeded its total deadline")
            raise StopAsyncIteration
        next_chunk = getattr(self._iterator, "__anext__", None)
        if not callable(next_chunk):
            await self.aclose()
            raise VoiceProfileUnavailable("Pipecat Groq response omitted async iteration")
        try:
            async with asyncio.timeout_at(self._iteration_deadline):
                return await next_chunk()
        except StopAsyncIteration:
            await self.aclose()
            if self._expired:
                raise TimeoutError("Pipecat Groq stream exceeded its total deadline") from None
            raise
        except asyncio.CancelledError as exc:
            await self._close_after_terminal_error(exc)
            raise
        except Exception as exc:
            await self._close_after_terminal_error(exc)
            raise

    async def aclose(self) -> None:
        current_task = asyncio.current_task()
        watchdog = self._watchdog_task
        if watchdog is not current_task and not watchdog.done():
            watchdog.cancel()
        try:
            async with self._close_lock:
                if self._cleanup_complete:
                    return
                self._terminal = True
                loop = asyncio.get_running_loop()
                if loop.time() >= self._deadline:
                    raise TimeoutError("Pipecat Groq stream cleanup exceeded its total deadline")
                async with asyncio.timeout_at(self._deadline):
                    await _close_stream_objects(self._iterator, self._stream)
                self._cleanup_complete = True
        finally:
            if watchdog is not current_task:
                await asyncio.gather(watchdog, return_exceptions=True)

    async def _expire(self) -> None:
        try:
            await asyncio.sleep(
                max(0.0, self._iteration_deadline - asyncio.get_running_loop().time())
            )
            self._expired = True
            await self.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The active iterator reports the authoritative timeout. The
            # watchdog exists to close a stream even while its consumer is
            # between __anext__ calls.
            pass

    async def _close_after_terminal_error(self, primary: BaseException) -> None:
        try:
            await self.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as cleanup_error:
            primary.add_note(f"Pipecat Groq stream cleanup also failed: {cleanup_error!r}")


def _bounded_groq_adapter(service_type: type) -> type:
    """Apply one total-lifetime Groq bound without Pipecat's retry path."""

    class MurmurBoundedGroqLLMService(service_type):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            timeout = kwargs.get("retry_timeout_secs")
            super().__init__(*args, **kwargs)
            self._murmur_request_timeout_seconds = timeout

        def create_client(self, *args: Any, **kwargs: Any) -> object:
            client = super().create_client(*args, **kwargs)
            with_options = getattr(client, "with_options", None)
            if not callable(with_options):
                raise VoiceProfileUnavailable("Pipecat Groq client omitted retry controls")
            client = with_options(max_retries=0)
            if getattr(client, "max_retries", None) != 0:
                raise VoiceProfileUnavailable("Pipecat Groq client retained hidden retries")
            return client

        async def get_chat_completions(self, context: object) -> object:
            timeout = self._murmur_request_timeout_seconds
            if not isinstance(timeout, int | float) or not math.isfinite(timeout) or timeout <= 0:
                raise VoiceProfileUnavailable("Pipecat Groq request timeout is invalid")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            cleanup_reserve = min(
                _MAX_GROQ_STREAM_CLEANUP_RESERVE_SECONDS,
                max(0.001, timeout * 0.1),
            )
            iteration_deadline = deadline - cleanup_reserve
            async with asyncio.timeout_at(iteration_deadline):
                stream = await super().get_chat_completions(context)
            try:
                return _BoundedAsyncStream(
                    stream,
                    deadline=deadline,
                    iteration_deadline=iteration_deadline,
                )
            except Exception as exc:
                try:
                    async with asyncio.timeout_at(deadline):
                        await _close_stream_objects(stream)
                except Exception as cleanup_error:
                    exc.add_note(f"Pipecat Groq stream cleanup also failed: {cleanup_error!r}")
                raise

    MurmurBoundedGroqLLMService.__name__ = f"MurmurBounded{service_type.__name__}"
    return MurmurBoundedGroqLLMService


async def _close_stream_objects(*objects: object) -> None:
    seen: set[int] = set()
    errors: list[Exception] = []
    for item in objects:
        if id(item) in seen:
            continue
        seen.add(id(item))
        close = getattr(item, "aclose", None)
        if not callable(close):
            close = getattr(item, "close", None)
        if not callable(close):
            continue
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise VoiceProfileUnavailable("Pipecat Groq stream cleanup failed") from errors[0]


async def _invoke_cleanup(close: Callable[[], object]) -> None:
    result = close()
    if inspect.isawaitable(result):
        await result


def _consume_background_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


def _validate_cleanup_timeout(seconds: float) -> None:
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int | float)
        or not math.isfinite(seconds)
        or not 0 < seconds <= _MAX_PROVIDER_CLEANUP_TIMEOUT_SECONDS
    ):
        raise ValueError("Pipecat provider cleanup timeout must be between zero and 15 seconds")


def _validate_factory_surface(factories: PipecatComponentFactories) -> None:
    for name in ("validate_available", "make_stt", "make_llm", "make_tts"):
        if not callable(getattr(factories, name, None)):
            raise VoiceProfileUnavailable(f"Pipecat component factory is missing {name}")


def _require_real_secret_or_id(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise VoiceProfileUnavailable(f"Pipecat {name} is required")
    normalized = value.strip().casefold()
    if (
        normalized in _PLACEHOLDERS
        or normalized.startswith(("test-", "fake-", "dummy-", "placeholder-"))
        or "replace" in normalized
    ):
        raise VoiceProfileUnavailable(f"Pipecat {name} must not be a placeholder")


def _first_leaf_exception(group: ExceptionGroup) -> Exception:
    for error in group.exceptions:
        if isinstance(error, ExceptionGroup):
            return _first_leaf_exception(error)
        return error
    return group

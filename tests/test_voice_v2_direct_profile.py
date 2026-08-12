"""Provider-free contracts for the exact LiveKit direct cascade profile."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from murmur.voice.profile import VoiceProfileScope, VoiceProfileUnavailable
from murmur.voice.provider_probe import ProviderProbeError, visible_string_ids
from murmur.voice.provider_profiles.livekit_cascade import (
    DIRECT_CASCADE_PROFILE_ID,
    DirectCascadeSettings,
    LiveKitDirectCascadeProvider,
    LiveKitPluginFactories,
    _IdempotentComponentCloser,
    build_direct_cascade_provider,
    build_direct_cascade_provider_from_config,
)


def _settings(**changes: object) -> DirectCascadeSettings:
    settings = DirectCascadeSettings(
        profile_id=DIRECT_CASCADE_PROFILE_ID,
        deepgram_api_key="deepgram-real-key-123",
        groq_api_key="groq-real-key-123",
        elevenlabs_api_key="eleven-real-key-123",
        elevenlabs_voice_id="voice-real-id-123",
        probe_timeout_seconds=0.5,
    )
    return replace(settings, **changes)


def _scope(profile_id: str = DIRECT_CASCADE_PROFILE_ID) -> VoiceProfileScope:
    return VoiceProfileScope(
        profile_id=profile_id,
        user_id="user-1",
        session_id="session-1",
        agent_id="agent-1",
        voice_call_id="call-1",
        trace_id="trace-1",
        system_prompt="Be concise, accurate, and conversational.",
    )


def _response(url: str, settings: DirectCascadeSettings) -> object:
    if url.endswith("deepgram.com/v1/auth/token"):
        # The official response is key metadata, not a bearer token contract.
        return {"api_key_id": "key-id", "comment": "murmur voice v2"}
    if url.endswith("deepgram.com/v1/models"):
        return {
            "stt": [
                {
                    "name": "general",
                    "canonical_name": "nova-3-general",
                    "streaming": True,
                }
            ]
        }
    if url.endswith("groq.com/openai/v1/models"):
        return {"data": [{"id": settings.groq_model}]}
    if url.endswith("elevenlabs.io/v1/models"):
        return [{"model_id": settings.elevenlabs_model}]
    if url.endswith("elevenlabs.io/v1/voices"):
        return {"voices": [{"voice_id": settings.elevenlabs_voice_id}]}
    raise AssertionError(f"unexpected provider probe URL: {url}")


class RecordingProbeTransport:
    def __init__(self, settings: DirectCascadeSettings) -> None:
        self.settings = settings
        self.calls: list[tuple[str, dict[str, str], float]] = []

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Any:
        self.calls.append((url, dict(headers), timeout_seconds))
        return _response(url, self.settings)


class BlockingProbeTransport(RecordingProbeTransport):
    """All five requests must be scheduled before any can complete."""

    def __init__(self, settings: DirectCascadeSettings) -> None:
        super().__init__(settings)
        self.all_started = asyncio.Event()

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Any:
        self.calls.append((url, dict(headers), timeout_seconds))
        if len(self.calls) == 5:
            self.all_started.set()
        await self.all_started.wait()
        return _response(url, self.settings)


class HangingProbeTransport:
    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Any:
        del url, headers, timeout_seconds
        await asyncio.Event().wait()


class FailFastCancellingProbeTransport(RecordingProbeTransport):
    """One request fails after every sibling starts; all siblings observe cancellation."""

    def __init__(self, settings: DirectCascadeSettings) -> None:
        super().__init__(settings)
        self.all_started = asyncio.Event()
        self.cancelled_urls: set[str] = set()
        self.finished_urls: set[str] = set()

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Any:
        self.calls.append((url, dict(headers), timeout_seconds))
        if len(self.calls) == 5:
            self.all_started.set()
        await self.all_started.wait()
        if url.endswith("groq.com/openai/v1/models"):
            raise ProviderProbeError("fast catalog failure")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled_urls.add(url)
            raise
        finally:
            self.finished_urls.add(url)


class CloseableComponent:
    def __init__(self, name: str, close_order: list[str]) -> None:
        self.name = name
        self.close_calls = 0
        self._close_order = close_order

    async def aclose(self) -> None:
        self.close_calls += 1
        self._close_order.append(self.name)


class CancelOnceComponent(CloseableComponent):
    def __init__(self, name: str, close_order: list[str]) -> None:
        super().__init__(name, close_order)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = False

    async def aclose(self) -> None:
        self.close_calls += 1
        self.started.set()
        await self.release.wait()
        self.completed = True
        self._close_order.append(self.name)


class FailOnceComponent(CloseableComponent):
    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise RuntimeError("transient cleanup failure")
        self._close_order.append(self.name)


class RecordingFactories:
    def __init__(self, *, fail_at: str | None = None, close_failure: bool = False) -> None:
        self.fail_at = fail_at
        self.close_failure = close_failure
        self.calls: list[tuple[str, DirectCascadeSettings]] = []
        self.close_order: list[str] = []
        self.components: dict[str, CloseableComponent] = {}

    def _make(self, name: str, settings: DirectCascadeSettings) -> object:
        self.calls.append((name, settings))
        if self.fail_at == name:
            raise RuntimeError(f"{name} construction failed")
        component = CloseableComponent(name, self.close_order)
        if self.close_failure and name == "stt":

            async def fail_close() -> None:
                component.close_calls += 1
                component._close_order.append(component.name)
                raise RuntimeError("cleanup failed")

            component.aclose = fail_close  # type: ignore[method-assign]
        self.components[name] = component
        return component

    def make_stt(self, settings: DirectCascadeSettings) -> object:
        return self._make("stt", settings)

    def make_llm(self, settings: DirectCascadeSettings) -> object:
        return self._make("llm", settings)

    def make_tts(self, settings: DirectCascadeSettings) -> object:
        return self._make("tts", settings)


@pytest.mark.asyncio
async def test_admission_is_static_exact_and_network_free() -> None:
    settings = _settings()
    probes = RecordingProbeTransport(settings)
    factories = RecordingFactories()
    provider = build_direct_cascade_provider(
        settings,
        factories=factories,
        probe_transport=probes,
    )

    admission = await provider.admit(_scope())

    assert admission.profile_id == DIRECT_CASCADE_PROFILE_ID
    assert admission.required_components == ("stt", "llm", "tts")
    assert len(admission.config_hash) == 64
    assert settings.deepgram_api_key not in admission.config_hash
    assert probes.calls == []
    assert factories.calls == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("profile_id", "some-other-profile", "requires profile_id"),
        ("deepgram_model", "nova-2", "requires deepgram_model"),
        ("deepgram_language", "en-US", "requires deepgram_language"),
        ("deepgram_endpointing_ms", 700, "requires deepgram_endpointing_ms"),
        ("groq_model", "llama-3.3-70b-versatile", "requires groq_model"),
        ("elevenlabs_model", "eleven_turbo_v2_5", "requires elevenlabs_model"),
        ("elevenlabs_encoding", "mp3_44100_128", "requires elevenlabs_encoding"),
        ("deepgram_api_key", "your-api-key", "placeholder"),
        ("deepgram_api_key", "test-deepgram-key", "placeholder"),
        ("groq_api_key", "", "required"),
        ("elevenlabs_api_key", "placeholder-value", "placeholder"),
        ("elevenlabs_voice_id", "change-me", "placeholder"),
        ("probe_timeout_seconds", 0, "between zero and 15"),
        ("probe_timeout_seconds", float("nan"), "between zero and 15"),
    ],
)
def test_settings_fail_closed_on_drift_placeholders_and_invalid_bounds(
    field: str,
    value: object,
    message: str,
) -> None:
    settings = _settings(**{field: value})

    with pytest.raises(VoiceProfileUnavailable, match=message):
        build_direct_cascade_provider(settings, factories=RecordingFactories())


@pytest.mark.asyncio
async def test_prepare_runs_concurrent_metadata_probes_then_builds_exact_profile() -> None:
    settings = _settings()
    probes = BlockingProbeTransport(settings)
    factories = RecordingFactories()
    provider = LiveKitDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=probes,
    )

    prepared = await provider.prepare(_scope())

    assert len(probes.calls) == 5
    assert all(call[2] == settings.probe_timeout_seconds for call in probes.calls)
    assert any(
        call[1] == {"Authorization": f"Token {settings.deepgram_api_key}"} for call in probes.calls
    )
    assert any(
        call[1] == {"Authorization": f"Bearer {settings.groq_api_key}"} for call in probes.calls
    )
    assert sum(call[1] == {"xi-api-key": settings.elevenlabs_api_key} for call in probes.calls) == 2
    assert [name for name, passed_settings in factories.calls if passed_settings is settings] == [
        "stt",
        "llm",
        "tts",
    ]
    assert prepared.instructions == _scope().system_prompt
    assert prepared.stt is factories.components["stt"]
    assert prepared.llm is factories.components["llm"]
    assert prepared.tts is factories.components["tts"]
    assert prepared.vad is None
    assert prepared.session_policy is not None
    assert prepared.session_policy.turn_detection == "stt"
    assert prepared.session_policy.preemptive_generation is False
    assert prepared.media_policy is not None
    assert prepared.media_policy.input_sample_rate == 16_000
    assert prepared.media_policy.output_sample_rate == 24_000
    assert prepared.connection_policy is not None
    assert prepared.connection_policy.stt.max_retry == 1
    assert prepared.connection_policy.llm.timeout_seconds == 8.0

    readiness = prepared.readiness
    assert readiness is not None
    assert readiness.config_hash == settings.config_hash()
    assert [(item.component, item.provider, item.model) for item in readiness.provider_models] == [
        ("stt", "deepgram", "nova-3"),
        ("llm", "groq", "openai/gpt-oss-120b"),
        ("tts", "elevenlabs", "eleven_flash_v2_5"),
    ]
    assert any("streaming" in limitation for limitation in readiness.limitations)
    assert any("latency" in limitation for limitation in readiness.limitations)


@pytest.mark.asyncio
async def test_deepgram_auth_accepts_nonempty_key_details_without_token_fields() -> None:
    settings = _settings()
    probes = RecordingProbeTransport(settings)
    prepared = await LiveKitDirectCascadeProvider(
        settings,
        factories=RecordingFactories(),
        probe_transport=probes,
    ).prepare(_scope())

    assert prepared.readiness is not None
    assert prepared.readiness.provider_models[0].provider == "deepgram"
    assert not hasattr(prepared, "api_key_id")
    assert all("key-id" not in value for value in prepared.readiness.limitations)


@pytest.mark.asyncio
async def test_deepgram_auth_rejects_empty_or_non_mapping_key_details() -> None:
    settings = _settings()

    class AuthResponseTransport(RecordingProbeTransport):
        def __init__(self, response: object) -> None:
            super().__init__(settings)
            self.response = response

        async def get_json(
            self,
            url: str,
            *,
            headers: dict[str, str],
            timeout_seconds: float,
        ) -> Any:
            if url.endswith("deepgram.com/v1/auth/token"):
                return self.response
            return await super().get_json(
                url,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )

    for response in ({}, []):
        with pytest.raises(VoiceProfileUnavailable, match="metadata readiness failed") as error:
            await LiveKitDirectCascadeProvider(
                settings,
                factories=RecordingFactories(),
                probe_transport=AuthResponseTransport(response),
            ).prepare(_scope())
        assert isinstance(error.value.__cause__, ProviderProbeError)
        assert "key details" in str(error.value.__cause__)


@pytest.mark.asyncio
async def test_prepared_cleanup_is_job_owned_reverse_order_and_idempotent() -> None:
    settings = _settings()
    factories = RecordingFactories()
    prepared = await LiveKitDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=RecordingProbeTransport(settings),
    ).prepare(_scope())

    assert prepared.close_callback is not None
    await asyncio.gather(prepared.close_callback(), prepared.close_callback())

    assert factories.close_order == ["tts", "llm", "stt"]
    assert all(component.close_calls == 1 for component in factories.components.values())


@pytest.mark.asyncio
async def test_cancelled_cleanup_resumes_without_duplicates() -> None:
    close_order: list[str] = []
    stt = CloseableComponent("stt", close_order)
    llm = CancelOnceComponent("llm", close_order)
    tts = CloseableComponent("tts", close_order)
    components: list[object] = [stt, llm, tts]

    closer = _IdempotentComponentCloser(components)
    first_close = asyncio.create_task(closer())
    await llm.started.wait()
    first_close.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first_close

    assert tts.close_calls == 1
    assert llm.close_calls == 1
    assert stt.close_calls == 0
    assert close_order == ["tts"]

    llm.release.set()
    await closer()
    await closer()

    assert tts.close_calls == 1
    assert llm.close_calls == 2
    assert stt.close_calls == 1
    assert close_order == ["tts", "llm", "stt"]


@pytest.mark.asyncio
async def test_failed_cleanup_retry_does_not_reclose_completed_components() -> None:
    close_order: list[str] = []
    stt = CloseableComponent("stt", close_order)
    llm = FailOnceComponent("llm", close_order)
    tts = CloseableComponent("tts", close_order)
    closer = _IdempotentComponentCloser([stt, llm, tts])

    with pytest.raises(VoiceProfileUnavailable, match="cleanup failed") as error:
        await closer()

    assert isinstance(error.value.__cause__, RuntimeError)
    assert close_order == ["tts", "stt"]
    assert (tts.close_calls, llm.close_calls, stt.close_calls) == (1, 1, 1)

    await closer()
    await closer()

    assert close_order == ["tts", "stt", "llm"]
    assert (tts.close_calls, llm.close_calls, stt.close_calls) == (1, 2, 1)


@pytest.mark.asyncio
async def test_partial_construction_closes_completed_components_and_preserves_root_error() -> None:
    settings = _settings()
    factories = RecordingFactories(fail_at="llm", close_failure=True)
    provider = LiveKitDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=RecordingProbeTransport(settings),
    )

    with pytest.raises(RuntimeError, match="llm construction failed"):
        await provider.prepare(_scope())

    assert factories.close_order == ["stt"]
    assert factories.components["stt"].close_calls == 1
    assert [name for name, _ in factories.calls] == ["stt", "llm"]


@pytest.mark.asyncio
async def test_partial_construction_preserves_cancellation_after_cleanup() -> None:
    settings = _settings()

    class CancellingFactories(RecordingFactories):
        def make_llm(self, settings: DirectCascadeSettings) -> object:
            self.calls.append(("llm", settings))
            raise asyncio.CancelledError

    factories = CancellingFactories()
    provider = LiveKitDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=RecordingProbeTransport(settings),
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.prepare(_scope())

    assert factories.close_order == ["stt"]
    assert factories.components["stt"].close_calls == 1
    assert [name for name, _ in factories.calls] == ["stt", "llm"]


@pytest.mark.asyncio
async def test_probe_timeout_fails_before_component_construction() -> None:
    settings = _settings(probe_timeout_seconds=0.01)
    factories = RecordingFactories()
    provider = LiveKitDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=HangingProbeTransport(),
    )

    with pytest.raises(VoiceProfileUnavailable, match="metadata probes timed out"):
        await provider.prepare(_scope())

    assert factories.calls == []


@pytest.mark.asyncio
async def test_probe_fast_failure_cancels_and_awaits_every_key_bearing_sibling() -> None:
    settings = _settings()
    probes = FailFastCancellingProbeTransport(settings)
    provider = LiveKitDirectCascadeProvider(
        settings,
        factories=RecordingFactories(),
        probe_transport=probes,
    )

    with pytest.raises(VoiceProfileUnavailable, match="metadata readiness failed"):
        await provider.prepare(_scope())

    failed = next(url for url, _, _ in probes.calls if url.endswith("groq.com/openai/v1/models"))
    siblings = {url for url, _, _ in probes.calls} - {failed}
    assert len(probes.calls) == 5
    assert probes.cancelled_urls == siblings
    assert probes.finished_urls == siblings
    current = asyncio.current_task()
    assert not [task for task in asyncio.all_tasks() if task is not current and not task.done()]


@pytest.mark.asyncio
async def test_probe_timeout_cancels_and_awaits_all_requests() -> None:
    settings = _settings(probe_timeout_seconds=0.01)

    class TimeoutObservingTransport(RecordingProbeTransport):
        def __init__(self) -> None:
            super().__init__(settings)
            self.cancelled = 0

        async def get_json(
            self,
            url: str,
            *,
            headers: dict[str, str],
            timeout_seconds: float,
        ) -> Any:
            self.calls.append((url, dict(headers), timeout_seconds))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise

    probes = TimeoutObservingTransport()
    with pytest.raises(VoiceProfileUnavailable, match="metadata probes timed out"):
        await LiveKitDirectCascadeProvider(
            settings,
            factories=RecordingFactories(),
            probe_transport=probes,
        ).prepare(_scope())

    assert len(probes.calls) == 5
    assert probes.cancelled == 5


@pytest.mark.asyncio
async def test_catalog_mismatch_fails_before_component_construction() -> None:
    settings = _settings()
    factories = RecordingFactories()

    class WrongCatalog(RecordingProbeTransport):
        async def get_json(
            self,
            url: str,
            *,
            headers: dict[str, str],
            timeout_seconds: float,
        ) -> Any:
            payload = await super().get_json(
                url,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )
            if url.endswith("groq.com/openai/v1/models"):
                return {"data": [{"id": "some-other-model"}]}
            return payload

    provider = LiveKitDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=WrongCatalog(settings),
    )

    with pytest.raises(VoiceProfileUnavailable, match="metadata readiness failed") as exc_info:
        await provider.prepare(_scope())

    assert isinstance(exc_info.value.__cause__, ProviderProbeError)
    assert factories.calls == []


@pytest.mark.asyncio
async def test_scope_mismatch_and_empty_prompt_fail_before_network() -> None:
    settings = _settings()
    probes = RecordingProbeTransport(settings)
    provider = LiveKitDirectCascadeProvider(
        settings,
        factories=RecordingFactories(),
        probe_transport=probes,
    )

    with pytest.raises(VoiceProfileUnavailable, match="scope mismatch"):
        await provider.prepare(_scope("some-other-profile"))
    with pytest.raises(VoiceProfileUnavailable, match="system prompt is empty"):
        await provider.prepare(replace(_scope(), system_prompt="   "))

    assert probes.calls == []


def test_default_plugin_import_boundary_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    factories = LiveKitPluginFactories()

    def unavailable_plugins() -> tuple[Any, Any, Any]:
        raise VoiceProfileUnavailable("plugins unavailable")

    monkeypatch.setattr(factories, "_plugins", unavailable_plugins)

    with pytest.raises(VoiceProfileUnavailable, match="plugins unavailable"):
        asyncio.run(
            LiveKitDirectCascadeProvider(
                _settings(),
                factories=factories,
                probe_transport=RecordingProbeTransport(_settings()),
            ).admit(_scope())
        )


def test_config_adapter_maps_existing_names_without_importing_global_config() -> None:
    class Config:
        VOICE_V2_PROFILE_ID = DIRECT_CASCADE_PROFILE_ID
        VOICE_V2_PROVIDER_PROBE_TIMEOUT_SECONDS = "3.5"
        DEEPGRAM_KEY = "deepgram-real-key-123"
        GROQ_API_KEY = "groq-real-key-123"
        ELEVENLABS_API_KEY = "eleven-real-key-123"
        ELEVENLABS_VOICE_ID = "voice-real-id-123"

    provider = build_direct_cascade_provider_from_config(Config)

    assert provider._settings == _settings(probe_timeout_seconds=3.5)  # type: ignore[attr-defined]


def test_visible_string_ids_is_strict_deduplicated_and_never_coerces_values() -> None:
    payload = {
        "data": [
            {"id": "model-a"},
            {"id": " model-a "},
            {"id": 123},
            {"name": "model-b"},
            "invalid",
        ]
    }

    assert visible_string_ids(payload, collection_key="data", id_keys=("id", "name")) == (
        "model-a",
        "model-b",
    )
    with pytest.raises(ProviderProbeError, match="missing list field"):
        visible_string_ids({}, collection_key="data")

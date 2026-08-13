"""Provider-free tests for the pinned Pipecat direct cascade profile."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from murmur.voice.profile import VoiceProfileScope, VoiceProfileUnavailable
from murmur.voice.provider_profiles.pipecat_cascade import (
    PIPECAT_DIRECT_CASCADE_PROFILE_ID,
    PipecatCascadeSettings,
    PipecatDirectCascadeProvider,
    PipecatServiceFactories,
    build_pipecat_cascade_provider,
)


def _settings(**changes: object) -> PipecatCascadeSettings:
    values: dict[str, object] = {
        "profile_id": PIPECAT_DIRECT_CASCADE_PROFILE_ID,
        "deepgram_api_key": "deepgram-real-key-123",
        "groq_api_key": "groq-real-key-123",
        "elevenlabs_api_key": "eleven-real-key-123",
        "elevenlabs_voice_id": "voice-real-id-123",
    }
    values.update(changes)
    return PipecatCascadeSettings(**values)  # type: ignore[arg-type]


def _scope() -> VoiceProfileScope:
    return VoiceProfileScope(
        profile_id=PIPECAT_DIRECT_CASCADE_PROFILE_ID,
        user_id="firebase-user-1",
        session_id="00000000-0000-4000-8000-000000000001",
        agent_id="00000000-0000-4000-8000-000000000002",
        voice_call_id="00000000-0000-4000-8000-000000000003",
        trace_id="00000000-0000-4000-8000-000000000004",
        system_prompt="Answer briefly and accurately.",
    )


class Closeable:
    def __init__(self, name: str, order: list[str], *, cleanup_failures: int = 0) -> None:
        self.name = name
        self.order = order
        self.close_calls = 0
        self.cleanup_failures = cleanup_failures
        self.stream_ready = asyncio.Event()
        self.event_handlers: dict[str, list[object]] = {}

    async def cleanup(self) -> None:
        self.close_calls += 1
        if self.close_calls <= self.cleanup_failures:
            raise RuntimeError(f"{self.name} cleanup failed")
        self.order.append(self.name)

    async def wait_stream_ready(self) -> None:
        await self.stream_ready.wait()

    def add_event_handler(self, event_name: str, handler: object) -> None:
        self.event_handlers.setdefault(event_name, []).append(handler)

    async def emit(self, event_name: str, *args: object) -> None:
        for handler in self.event_handlers.get(event_name, []):
            result = handler(self, *args)  # type: ignore[operator]
            if asyncio.iscoroutine(result):
                await result


class RecordingFactories:
    def __init__(
        self,
        *,
        fail_at: str | None = None,
        cleanup_failures: dict[str, int] | None = None,
    ) -> None:
        self.calls: list[tuple[str, object]] = []
        self.close_order: list[str] = []
        self.components: dict[str, Closeable] = {}
        self.fail_at = fail_at
        self.cleanup_failures = cleanup_failures or {}

    def validate_available(self) -> None:
        self.calls.append(("validate", None))

    def _make(self, name: str, extra: object) -> Closeable:
        self.calls.append((name, extra))
        if name == self.fail_at:
            raise RuntimeError(f"{name} failed")
        component = Closeable(
            name,
            self.close_order,
            cleanup_failures=self.cleanup_failures.get(name, 0),
        )
        self.components[name] = component
        return component

    def make_stt(self, settings: PipecatCascadeSettings) -> object:
        return self._make("stt", settings)

    def make_llm(self, settings: PipecatCascadeSettings) -> object:
        return self._make("llm", settings)

    def make_tts(self, settings: PipecatCascadeSettings) -> object:
        return self._make("tts", settings)


class RecordingProbe:
    def __init__(self, settings: PipecatCascadeSettings) -> None:
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
        if url.endswith("/auth/token"):
            return {"status": "accepted"}
        if url.endswith("deepgram.com/v1/models"):
            return {"stt": [{"canonical_name": "nova-3"}]}
        if url.endswith("groq.com/openai/v1/models"):
            return {"data": [{"id": self.settings.groq_model}]}
        if url.endswith("/models"):
            return [{"model_id": self.settings.elevenlabs_model}]
        if url.endswith("/voices"):
            return {"voices": [{"voice_id": self.settings.elevenlabs_voice_id}]}
        raise AssertionError(url)


@pytest.mark.asyncio
async def test_admission_is_static_exact_and_does_not_construct_services() -> None:
    settings = _settings()
    factories = RecordingFactories()
    probes = RecordingProbe(settings)
    provider = build_pipecat_cascade_provider(
        settings,
        factories=factories,
        probe_transport=probes,
    )

    admission = await provider.admit(_scope())

    assert admission.profile_id == PIPECAT_DIRECT_CASCADE_PROFILE_ID
    assert admission.required_components == ("stt", "llm", "tts")
    assert len(admission.config_hash) == 64
    assert probes.calls == []
    assert factories.calls == [("validate", None)]
    assert settings.deepgram_api_key not in admission.config_hash


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("profile_id", "livekit-agents-cascade-v1", "requires profile_id"),
        ("deepgram_model", "nova-2", "requires deepgram_model"),
        ("groq_model", "other", "requires groq_model"),
        ("elevenlabs_model", "other", "requires elevenlabs_model"),
        ("deepgram_api_key", "test-key", "placeholder"),
        ("groq_api_key", "", "required"),
        ("probe_timeout_seconds", float("nan"), "between zero and 15"),
    ],
)
def test_settings_fail_closed_on_manifest_drift_and_placeholders(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(VoiceProfileUnavailable, match=message):
        build_pipecat_cascade_provider(
            _settings(**{field: value}),
            factories=RecordingFactories(),
        )


def test_settings_repr_never_exposes_provider_api_keys() -> None:
    settings = _settings()

    rendered = repr(settings)

    assert settings.deepgram_api_key not in rendered
    assert settings.groq_api_key not in rendered
    assert settings.elevenlabs_api_key not in rendered
    assert "deepgram_api_key" not in rendered
    assert "groq_api_key" not in rendered
    assert "elevenlabs_api_key" not in rendered


@pytest.mark.asyncio
async def test_prepare_probes_authoritatively_then_constructs_and_closes_once() -> None:
    settings = _settings()
    factories = RecordingFactories()
    probes = RecordingProbe(settings)
    prepared = await PipecatDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=probes,
    ).prepare(_scope())

    assert len(probes.calls) == 5
    assert [name for name, _ in factories.calls] == ["validate", "stt", "llm", "tts"]
    assert prepared.instructions == _scope().system_prompt
    assert prepared.media_policy.input_sample_rate == 16_000
    assert prepared.media_policy.output_sample_rate == 24_000
    assert prepared.connection_policy.llm.max_retry == 0
    assert prepared.connection_policy.llm.timeout_seconds == 8.0
    assert any("metadata-only" in limitation for limitation in prepared.readiness.limitations)
    assert any(
        "aggregate runtime readiness deadline" in limitation
        for limitation in prepared.readiness.limitations
    )
    assert [
        (item.component, item.provider, item.model) for item in prepared.readiness.provider_models
    ] == [
        ("stt", "deepgram", "nova-3"),
        ("llm", "groq", "openai/gpt-oss-120b"),
        ("tts", "elevenlabs", "eleven_flash_v2_5"),
    ]

    stream_wait = asyncio.create_task(prepared.wait_streams_ready())
    await asyncio.sleep(0)
    assert not stream_wait.done()
    factories.components["stt"].stream_ready.set()
    await factories.components["tts"].emit("on_connected")
    await asyncio.wait_for(stream_wait, timeout=1)

    await asyncio.gather(prepared.close_callback(), prepared.close_callback())

    assert factories.close_order == ["tts", "llm", "stt"]
    assert all(item.close_calls == 1 for item in factories.components.values())


@pytest.mark.asyncio
async def test_stream_readiness_surfaces_elevenlabs_public_connection_error() -> None:
    settings = _settings()
    factories = RecordingFactories()
    prepared = await PipecatDirectCascadeProvider(
        settings,
        factories=factories,
        probe_transport=RecordingProbe(settings),
    ).prepare(_scope())
    factories.components["stt"].stream_ready.set()
    await factories.components["tts"].emit("on_connection_error", "invalid websocket token")

    with pytest.raises(VoiceProfileUnavailable, match="provider streams failed readiness") as error:
        await prepared.wait_streams_ready()
    assert isinstance(error.value.__cause__, VoiceProfileUnavailable)


@pytest.mark.asyncio
async def test_partial_construction_closes_completed_services() -> None:
    settings = _settings()
    factories = RecordingFactories(fail_at="llm")
    with pytest.raises(RuntimeError, match="llm failed"):
        await PipecatDirectCascadeProvider(
            settings,
            factories=factories,
            probe_transport=RecordingProbe(settings),
        ).prepare(_scope())

    assert factories.close_order == ["stt"]
    assert factories.components["stt"].close_calls == 1


@pytest.mark.asyncio
async def test_partial_construction_retries_transient_cleanup_failure() -> None:
    settings = _settings()
    factories = RecordingFactories(fail_at="llm", cleanup_failures={"stt": 1})

    with pytest.raises(RuntimeError, match="llm failed"):
        await PipecatDirectCascadeProvider(
            settings,
            factories=factories,
            probe_transport=RecordingProbe(settings),
        ).prepare(_scope())

    assert factories.components["stt"].close_calls == 2
    assert factories.close_order == ["stt"]


@pytest.mark.asyncio
async def test_partial_construction_surfaces_persistent_cleanup_failure() -> None:
    settings = _settings()
    factories = RecordingFactories(fail_at="llm", cleanup_failures={"stt": 2})

    with pytest.raises(
        VoiceProfileUnavailable,
        match="construction failed and partial cleanup failed",
    ) as error:
        await PipecatDirectCascadeProvider(
            settings,
            factories=factories,
            probe_transport=RecordingProbe(settings),
        ).prepare(_scope())

    assert isinstance(error.value.__cause__, ExceptionGroup)
    assert factories.components["stt"].close_calls == 2


@pytest.mark.asyncio
async def test_partial_construction_cleanup_is_bounded_when_component_never_settles() -> None:
    settings = _settings()
    factories = RecordingFactories(fail_at="llm")

    async def never_settles() -> None:
        factories.components["stt"].close_calls += 1
        await asyncio.Event().wait()

    original_make_stt = factories.make_stt

    def make_hanging_stt(config: PipecatCascadeSettings) -> object:
        component = original_make_stt(config)
        component.cleanup = never_settles  # type: ignore[method-assign]
        return component

    factories.make_stt = make_hanging_stt  # type: ignore[method-assign]

    with pytest.raises(
        VoiceProfileUnavailable,
        match="construction failed and partial cleanup failed",
    ):
        async with asyncio.timeout(0.2):
            await PipecatDirectCascadeProvider(
                settings,
                factories=factories,
                probe_transport=RecordingProbe(settings),
                cleanup_timeout_seconds=0.01,
            ).prepare(_scope())

    assert factories.components["stt"].close_calls == 2


@pytest.mark.asyncio
async def test_timeout_cancels_all_metadata_requests_before_construction() -> None:
    settings = _settings(probe_timeout_seconds=0.01)
    factories = RecordingFactories()
    cancelled = 0

    class HangingProbe:
        async def get_json(self, *_args: object, **_kwargs: object) -> object:
            nonlocal cancelled
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled += 1
                raise

    with pytest.raises(VoiceProfileUnavailable, match="metadata probes timed out"):
        await PipecatDirectCascadeProvider(
            settings,
            factories=factories,
            probe_transport=HangingProbe(),  # type: ignore[arg-type]
        ).prepare(_scope())

    assert cancelled == 5
    assert [name for name, _ in factories.calls] == ["validate"]


@pytest.mark.asyncio
async def test_scope_mismatch_and_empty_prompt_fail_before_probe() -> None:
    settings = _settings()
    probes = RecordingProbe(settings)
    provider = PipecatDirectCascadeProvider(
        settings,
        factories=RecordingFactories(),
        probe_transport=probes,
    )

    with pytest.raises(VoiceProfileUnavailable, match="scope mismatch"):
        await provider.admit(replace(_scope(), profile_id="other"))
    with pytest.raises(VoiceProfileUnavailable, match="system prompt is empty"):
        await provider.prepare(replace(_scope(), system_prompt=" "))

    assert probes.calls == []


def test_public_service_factory_uses_canonical_17_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, dict[str, object]] = {}

    class Settings:
        def __init__(self, **kwargs: object) -> None:
            self.values = kwargs

    def service(name: str) -> type:
        class Service:
            def __init__(self, **kwargs: object) -> None:
                captured[name] = kwargs

        Service.Settings = Settings  # type: ignore[attr-defined]
        return Service

    factories = PipecatServiceFactories()
    monkeypatch.setattr(
        factories,
        "_services",
        lambda: (service("stt"), service("llm"), service("tts")),
    )
    settings = _settings()

    factories.make_stt(settings)
    factories.make_llm(settings)
    factories.make_tts(settings)

    assert captured["stt"]["sample_rate"] == 16_000
    assert captured["stt"]["settings"].values["endpointing"] == 300  # type: ignore[attr-defined]
    assert captured["llm"]["settings"].values == {  # type: ignore[attr-defined]
        "model": "openai/gpt-oss-120b",
    }
    assert captured["llm"]["retry_timeout_secs"] == 8.0
    assert captured["llm"]["retry_on_timeout"] is False
    assert captured["tts"]["sample_rate"] == 24_000
    assert captured["tts"]["settings"].values["voice"] == "voice-real-id-123"  # type: ignore[attr-defined]


def test_groq_adapter_disables_retries_at_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Settings:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class Client:
        def __init__(self, *, max_retries: int) -> None:
            self.max_retries = max_retries

        def with_options(self, **kwargs: object) -> Client:
            captured["options"] = kwargs
            return Client(max_retries=kwargs["max_retries"])  # type: ignore[arg-type]

    class Service:
        def __init__(self, **kwargs: object) -> None:
            captured["service"] = kwargs
            self._client = self.create_client(
                api_key=kwargs["api_key"],
                base_url="https://groq.invalid/openai/v1",
            )

        def create_client(self, **kwargs: object) -> Client:
            captured["client"] = kwargs
            return Client(max_retries=2)

    Service.Settings = Settings  # type: ignore[attr-defined]
    factories = PipecatServiceFactories()
    monkeypatch.setattr(factories, "_services", lambda: (Service, Service, Service))

    llm = factories.make_llm(_settings())

    assert captured["options"] == {"max_retries": 0}
    assert captured["client"] == {
        "api_key": "groq-real-key-123",
        "base_url": "https://groq.invalid/openai/v1",
    }
    assert llm._client.max_retries == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_real_pinned_groq_client_has_no_hidden_sdk_retries() -> None:
    llm = PipecatServiceFactories().make_llm(_settings())

    try:
        assert llm._retry_on_timeout is False  # type: ignore[attr-defined]
        assert llm._client.max_retries == 0  # type: ignore[attr-defined]
    finally:
        await llm._client.close()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_elevenlabs_adapter_translates_five_second_websocket_open_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Settings:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class Service:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def _websocket_connect(self, uri: str, **kwargs: object) -> object:
            captured["uri"] = uri
            captured.update(kwargs)
            return object()

    Service.Settings = Settings  # type: ignore[attr-defined]
    factories = PipecatServiceFactories()
    monkeypatch.setattr(factories, "_services", lambda: (Service, Service, Service))
    tts = factories.make_tts(_settings())

    await tts._websocket_connect("wss://example.invalid")  # type: ignore[attr-defined]

    assert captured["open_timeout"] == 5.0


@pytest.mark.asyncio
async def test_deepgram_adapter_waits_for_pinned_connection_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_connection_task = asyncio.Event()

    class Settings:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class Deepgram:
        def __init__(self, **_kwargs: object) -> None:
            self._connection_ready = asyncio.Event()
            self._connection_task = asyncio.create_task(release_connection_task.wait())

    class Service:
        def __init__(self, **_kwargs: object) -> None:
            pass

    Deepgram.Settings = Settings  # type: ignore[attr-defined]
    Service.Settings = Settings  # type: ignore[attr-defined]

    factories = PipecatServiceFactories()
    monkeypatch.setattr(factories, "_services", lambda: (Deepgram, Service, Service))
    stt = factories.make_stt(_settings())

    wait_task = asyncio.create_task(stt.wait_stream_ready())  # type: ignore[attr-defined]
    await asyncio.sleep(0)
    assert not wait_task.done()
    stt._connection_ready.set()  # type: ignore[attr-defined]
    await asyncio.wait_for(wait_task, timeout=1)
    release_connection_task.set()
    await stt._connection_task  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_deepgram_adapter_enforces_declared_initial_readiness_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Settings:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class Deepgram:
        def __init__(self, **_kwargs: object) -> None:
            self._connection_ready = asyncio.Event()
            self._connection_task = asyncio.create_task(asyncio.Event().wait())

    class Service:
        def __init__(self, **_kwargs: object) -> None:
            pass

    Deepgram.Settings = Settings  # type: ignore[attr-defined]
    Service.Settings = Settings  # type: ignore[attr-defined]
    factories = PipecatServiceFactories()
    monkeypatch.setattr(factories, "_services", lambda: (Deepgram, Service, Service))
    stt = factories.make_stt(_settings())
    stt._murmur_stream_readiness_timeout_seconds = 0.01  # type: ignore[attr-defined]

    with pytest.raises(VoiceProfileUnavailable, match="stream readiness timed out"):
        await stt.wait_stream_ready()  # type: ignore[attr-defined]

    stt._connection_task.cancel()  # type: ignore[attr-defined]
    await asyncio.gather(stt._connection_task, return_exceptions=True)  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("hanging_cleanup", [False, True])
async def test_groq_adapter_enforces_one_bounded_attempt(
    monkeypatch: pytest.MonkeyPatch,
    hanging_cleanup: bool,
) -> None:
    class Settings:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class Service:
        def __init__(self, **_kwargs: object) -> None:
            pass

    Service.Settings = Settings  # type: ignore[attr-defined]

    class HangingStream:
        def __init__(self) -> None:
            self.chunks = 0
            self.close_calls = 0

        def __aiter__(self) -> HangingStream:
            return self

        async def __anext__(self) -> object:
            self.chunks += 1
            if self.chunks == 1:
                return object()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def aclose(self) -> None:
            self.close_calls += 1
            if hanging_cleanup:
                await asyncio.Event().wait()

    stream = HangingStream()

    class HangingGroq(Service):
        calls = 0

        async def get_chat_completions(self, _context: object) -> object:
            self.calls += 1
            return stream

    factories = PipecatServiceFactories()
    monkeypatch.setattr(factories, "_services", lambda: (Service, HangingGroq, Service))
    llm = factories.make_llm(_settings())
    llm._murmur_request_timeout_seconds = 0.03  # type: ignore[attr-defined]

    bounded_stream = await llm.get_chat_completions(object())  # type: ignore[attr-defined]
    assert await anext(bounded_stream) is not None  # type: ignore[arg-type]
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            await anext(bounded_stream)  # type: ignore[arg-type]
    assert llm.calls == 1  # type: ignore[attr-defined]
    assert stream.close_calls >= 1
    await asyncio.sleep(0)
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name() == "pipecat-groq-stream-deadline"
    ]


@pytest.mark.asyncio
async def test_groq_adapter_reports_timeout_if_consumer_pauses_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Settings:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class Service:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class Stream:
        def __init__(self) -> None:
            self.close_calls = 0

        def __aiter__(self) -> Stream:
            return self

        async def __anext__(self) -> object:
            return object()

        async def aclose(self) -> None:
            self.close_calls += 1

    stream = Stream()

    class Groq(Service):
        calls = 0

        async def get_chat_completions(self, _context: object) -> object:
            self.calls += 1
            return stream

    Service.Settings = Settings  # type: ignore[attr-defined]
    factories = PipecatServiceFactories()
    monkeypatch.setattr(factories, "_services", lambda: (Service, Groq, Service))
    llm = factories.make_llm(_settings())
    llm._murmur_request_timeout_seconds = 0.03  # type: ignore[attr-defined]

    bounded_stream = await llm.get_chat_completions(object())  # type: ignore[attr-defined]
    assert await anext(bounded_stream) is not None  # type: ignore[arg-type]
    await asyncio.sleep(0.04)

    with pytest.raises(TimeoutError, match="total deadline"):
        await anext(bounded_stream)  # type: ignore[arg-type]

    assert llm.calls == 1  # type: ignore[attr-defined]
    assert stream.close_calls == 1

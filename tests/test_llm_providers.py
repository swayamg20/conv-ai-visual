"""Provider adapter contracts without external API calls."""

from types import SimpleNamespace

import pytest
from murmur.llm import GeminiClient, OpenAIClient, create_llm_client


@pytest.mark.asyncio
async def test_openai_stream_normalizes_text_tool_arguments_and_usage() -> None:
    client = OpenAIClient.__new__(OpenAIClient)

    async def chunks():
        yield SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content="Let me check. ",
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-1",
                                function=SimpleNamespace(
                                    name="search_resources",
                                    arguments='{"query":"free',
                                ),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ],
        )
        yield SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(
                                    name=None,
                                    arguments=' body"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
        )

    events = [event async for event in client.iter_stream_tool_events(chunks())]

    assert events[0] == {"type": "text_delta", "text": "Let me check. "}
    assert events[2] == {"type": "usage", "tokens_in": 12, "tokens_out": 4}
    completed = next(event for event in events if event["type"] == "tool_call_done")
    assert completed["tool_calls"][0].id == "call-1"
    assert completed["tool_calls"][0].name == "search_resources"
    assert completed["tool_calls"][0].arguments == {"query": "free body"}


@pytest.mark.asyncio
async def test_openai_text_stream_and_owned_http_client_close_on_consumer_abort() -> None:
    class ProviderStream:
        def __init__(self) -> None:
            self.closed = False
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="first patch"))]
            )

        async def close(self) -> None:
            self.closed = True

    provider_stream = ProviderStream()

    class Completions:
        async def create(self, **_kwargs):
            return provider_stream

    class HttpClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=Completions())
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    http_client = HttpClient()
    client = OpenAIClient.__new__(OpenAIClient)
    client.client = http_client
    client.model = "test-model"
    client.default_params = {}
    chunks = client.stream([{"role": "user", "content": "draw"}])

    assert await anext(chunks) == "first patch"
    await chunks.aclose()
    await client.aclose()

    assert provider_stream.closed is True
    assert http_client.closed is True


@pytest.mark.asyncio
async def test_gemini_text_stream_closes_provider_response_on_consumer_abort() -> None:
    class ProviderResponse:
        def __init__(self) -> None:
            self.closed = False
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return SimpleNamespace(text="first patch")

        async def aclose(self) -> None:
            self.closed = True

    provider_response = ProviderResponse()

    class Chat:
        async def send_message_async(self, *_args, **_kwargs):
            return provider_response

    client = GeminiClient.__new__(GeminiClient)
    client.model = SimpleNamespace(start_chat=lambda **_kwargs: Chat())
    client.default_params = {}
    chunks = client.stream([{"role": "user", "content": "draw"}])

    assert await anext(chunks) == "first patch"
    await chunks.aclose()

    assert provider_response.closed is True


def test_factory_routes_groq_through_openai_compatible_endpoint(monkeypatch) -> None:
    captured = {}

    def fake_openai_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr("murmur.llm.factory.OpenAIClient", fake_openai_client)

    client = create_llm_client("groq", api_key="key", model="model")

    assert client.api_key == "key"
    assert client.model == "model"
    assert captured["base_url"] == "https://api.groq.com/openai/v1"


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_llm_client("unknown", api_key="key", model="model")

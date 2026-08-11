"""Provider adapter contracts without external API calls."""

from types import SimpleNamespace

import pytest
from murmur.llm import OpenAIClient, create_llm_client


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

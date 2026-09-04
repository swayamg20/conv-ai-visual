"""Provider-specific controls for live-scene model clients."""


def scene_model_client_options(provider: str, model: str) -> dict[str, object]:
    """Return controls scoped to the selected live-scene provider and model."""

    if provider.casefold() == "azure_openai" and "gpt-oss" in model.casefold():
        return {
            "reasoning_effort": "low",
            # The semantic service owns its one explicit repair attempt. Hidden
            # SDK retries would make provider calls and paid spend uncountable.
            "transport_max_retries": 0,
        }
    return {}


__all__ = ["scene_model_client_options"]

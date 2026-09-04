"""Provider-specific controls for live-scene model clients."""


def scene_model_client_options(provider: str, model: str) -> dict[str, object]:
    """Return controls scoped to the selected live-scene provider and model."""

    normalized_provider = provider.casefold()
    if normalized_provider not in {"openai", "azure_openai", "groq", "gemini"}:
        return {}

    # The semantic service owns its one explicit repair attempt. Hidden SDK
    # retries would make provider calls and paid spend uncountable.
    options: dict[str, object] = {"transport_max_retries": 0}
    if normalized_provider == "azure_openai" and "gpt-oss" in model.casefold():
        options["reasoning_effort"] = "low"
    return options


__all__ = ["scene_model_client_options"]

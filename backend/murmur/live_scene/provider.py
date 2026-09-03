"""Provider-specific controls for live-scene model clients."""


def scene_model_client_options(provider: str, model: str) -> dict[str, str]:
    """Return controls scoped to the selected live-scene provider and model."""

    if provider.casefold() == "azure_openai" and "gpt-oss" in model.casefold():
        return {"reasoning_effort": "low"}
    return {}


__all__ = ["scene_model_client_options"]

"""Configuration-backed LLM provider factory."""

from funcs.config import config
from murmur.llm.base import LLMClient
from murmur.llm.gemini import GeminiClient
from murmur.llm.openai import OpenAIClient


def create_llm_client(
    provider: str, api_key: str | None = None, model: str | None = None, **kwargs
) -> LLMClient:
    """
    Factory function to create LLM client based on provider.

    Args:
        provider: Provider name ("openai" or "gemini")
        api_key: API key (fetched from config if not provided)
        model: Model name (fetched from config if not provided)
        **kwargs: Additional parameters to pass to client

    Returns:
        LLMClient instance

    Raises:
        ValueError: If provider is unsupported
    """
    provider = provider.lower()

    if provider == "openai":
        return OpenAIClient(
            api_key=api_key or config.OPENAI_API_KEY, model=model or config.OPENAI_MODEL, **kwargs
        )
    elif provider == "groq":
        return OpenAIClient(
            api_key=api_key or config.GROQ_API_KEY,
            model=model or config.GROQ_MODEL,
            base_url="https://api.groq.com/openai/v1",
            **kwargs,
        )
    elif provider == "gemini":
        return GeminiClient(
            api_key=api_key or config.GEMINI_API_KEY, model=model or config.GEMINI_MODEL, **kwargs
        )
    else:
        raise ValueError(
            f"Unsupported LLM provider: {provider}. Supported providers: openai, groq, gemini"
        )

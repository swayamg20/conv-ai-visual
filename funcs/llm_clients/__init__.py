from typing import Optional

from .base import LLMClient
from .openai_client import OpenAIClient
from .gemini_client import GeminiClient


def create_llm_client(
    provider: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs
) -> LLMClient:
    from funcs.config import config
    provider = provider.lower()
    if provider == "openai":
        return OpenAIClient(
            api_key=api_key or config.OPENAI_API_KEY,
            model=model or config.OPENAI_MODEL,
            **kwargs
        )
    elif provider == "gemini":
        return GeminiClient(
            api_key=api_key or config.GEMINI_API_KEY,
            model=model or config.GEMINI_MODEL,
            **kwargs
        )
    else:
        raise ValueError(
            f"Unsupported LLM provider: {provider}. "
            f"Supported providers: openai, gemini"
        )


__all__ = [
    "LLMClient",
    "OpenAIClient",
    "GeminiClient",
    "create_llm_client",
]

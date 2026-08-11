"""Provider-neutral LLM client surface."""

from murmur.llm.base import LLMClient
from murmur.llm.factory import create_llm_client
from murmur.llm.gemini import GeminiClient
from murmur.llm.openai import OpenAIClient

__all__ = ["GeminiClient", "LLMClient", "LLMPipeline", "OpenAIClient", "create_llm_client"]


def __getattr__(name: str):
    if name != "LLMPipeline":
        raise AttributeError(f"module 'murmur.llm' has no attribute {name!r}")
    from murmur.llm.pipeline import LLMPipeline

    return LLMPipeline

"""
Model Router — routes queries to fast or capable LLM based on complexity.

Default: fast model (Groq). Escalates to capable model (OpenAI) for complex reasoning.
"""

import re

from funcs.config import config

# Patterns that suggest complex reasoning needed
_ESCALATION_PATTERNS = [
    r"\bprove\b",
    r"\bderive\b",
    r"\bformal\b",
    r"step.by.step\b.*(?:integral|derivative|limit|proof)",
    r"compare.*(?:algorithm|approach|method).*(?:detail|depth)",
    r"\bdesign.*system\b",
    r"explain.*(?:in detail|in depth|thoroughly)",
    r"\bproof\b",
    r"\btheorem\b.*\bprove\b",
]

_compiled = [re.compile(p, re.IGNORECASE) for p in _ESCALATION_PATTERNS]


def route_model(user_message: str) -> tuple:
    """
    Determine which provider/model to use based on query complexity.

    Returns: (provider, api_key_or_None, model_or_None)
    - If escalation needed: returns OpenAI provider with its model
    - Otherwise: returns current default provider
    """
    for pattern in _compiled:
        if pattern.search(user_message):
            return ("openai", None, config.OPENAI_MODEL)

    # Default: fast model (whatever LLM_PROVIDER is set to)
    return (config.LLM_PROVIDER, None, None)

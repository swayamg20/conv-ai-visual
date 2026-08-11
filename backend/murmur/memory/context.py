"""Short-term conversation context and prompt-budget accounting."""

from typing import Any

MESSAGE_OVERHEAD_TOKENS = 4
REPLY_OVERHEAD_TOKENS = 3

_token_encoder = None
_token_encoder_checked = False


def _get_token_encoder():
    """Lazily load a shared tokenizer when available."""
    global _token_encoder, _token_encoder_checked
    if _token_encoder_checked:
        return _token_encoder

    _token_encoder_checked = True
    try:
        import tiktoken

        _token_encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _token_encoder = None
    return _token_encoder


def estimate_text_tokens(text: str) -> int:
    """Estimate token count with a tokenizer when available, otherwise fall back."""
    if not text:
        return 0

    encoder = _get_token_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass

    # Conservative character-based fallback that avoids a hard dependency.
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(message: dict[str, str]) -> int:
    """Estimate chat-format tokens for one role/content message."""
    return (
        MESSAGE_OVERHEAD_TOKENS
        + estimate_text_tokens(message.get("role", ""))
        + estimate_text_tokens(message.get("content", ""))
    )


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate total chat tokens for a message list."""
    return sum(estimate_message_tokens(message) for message in messages)


def _estimate_section_tokens(text: str) -> int:
    if not text:
        return 0
    return estimate_text_tokens(f"\n{text}")


def assemble_budgeted_system_prompt(
    base_system_prompt: str,
    prompt_sections: list[tuple[str, str]],
    current_messages: list[dict[str, str]],
    current_query: str,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """Add memory sections by priority without exceeding the context budget."""
    available_sections = [
        section_name for section_name, section_text in prompt_sections if section_text
    ]
    base_tokens = estimate_text_tokens(base_system_prompt)
    messages_tokens = estimate_messages_tokens(current_messages)
    query_tokens = estimate_message_tokens({"role": "user", "content": current_query})
    remaining_budget = max(
        0,
        max_tokens - base_tokens - messages_tokens - query_tokens - REPLY_OVERHEAD_TOKENS,
    )

    prompt_parts = [base_system_prompt]
    selected_sections: list[str] = []
    skipped_sections: list[str] = []
    section_token_map: dict[str, int] = {}
    section_tokens = 0

    for section_name, section_text in prompt_sections:
        if not section_text:
            continue
        tokens = _estimate_section_tokens(section_text)
        section_token_map[section_name] = tokens
        if tokens <= remaining_budget - section_tokens:
            prompt_parts.append(f"\n{section_text}")
            section_tokens += tokens
            selected_sections.append(section_name)
        else:
            skipped_sections.append(section_name)

    estimated_total = (
        base_tokens + messages_tokens + query_tokens + section_tokens + REPLY_OVERHEAD_TOKENS
    )
    metadata = {
        "base_tokens": base_tokens,
        "messages_tokens": messages_tokens,
        "query_tokens": query_tokens,
        "section_tokens": section_tokens,
        "estimated_total": estimated_total,
        "budget_tokens": max_tokens,
        "available_sections": available_sections,
        "selected_sections": selected_sections,
        "skipped_sections": skipped_sections,
        "section_token_map": section_token_map,
        "budget_exceeded": estimated_total > max_tokens,
    }
    return "\n".join(prompt_parts), metadata


class ConversationContext:
    """A token- and count-bounded sliding window of recent messages."""

    def __init__(self, max_messages: int = 20, max_tokens: int = 4000):
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.messages: list[dict[str, str]] = []
        self.system_prompt = ""

    def set_system_prompt(self, prompt: str) -> None:
        self.system_prompt = prompt

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self.trim()

    def trim(self) -> None:
        """Keep the newest messages within both count and token budgets."""
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages :]

        total_tokens = estimate_text_tokens(self.system_prompt) + REPLY_OVERHEAD_TOKENS
        total_tokens += estimate_messages_tokens(self.messages)

        while self.messages and total_tokens > self.max_tokens:
            removed = self.messages.pop(0)
            total_tokens -= estimate_message_tokens(removed)

    def get_messages(self) -> list[dict[str, str]]:
        """Return the system prompt followed by retained conversation messages."""
        return [{"role": "system", "content": self.system_prompt}, *self.messages]

    def clear(self) -> None:
        self.messages = []

    def get_recent_text(self, n: int = 5) -> str:
        """Return recent messages as text for summarization."""
        recent = self.messages[-n:] if len(self.messages) > n else self.messages
        return "\n".join(f"{message['role']}: {message['content']}" for message in recent)

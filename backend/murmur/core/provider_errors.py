"""Provider-neutral failures with no upstream payload or transport metadata."""

from enum import StrEnum


class LLMProviderFailureKind(StrEnum):
    """Closed categories safe to cross orchestration boundaries."""

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    INVALID_REQUEST = "invalid_request"
    SERVER = "server"
    UNKNOWN = "unknown"


class LLMProviderError(RuntimeError):
    """Sanitized provider failure carrying only its closed category."""

    def __init__(self, kind: LLMProviderFailureKind) -> None:
        if not isinstance(kind, LLMProviderFailureKind):
            raise TypeError("kind must be an LLMProviderFailureKind")
        self.kind = kind
        super().__init__(kind.value)


__all__ = ["LLMProviderError", "LLMProviderFailureKind"]

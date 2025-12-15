"""
Configuration module for voice AI application.
"""
import os
from typing import Optional


class Config:
    """Application configuration."""
    
    # Deepgram configuration
    DEEPGRAM_KEY: str = os.getenv("DEEPGRAM_KEY", "dea381e9d217d2451a3ef550b95b2735e58f101b")
    DEEPGRAM_MODEL: str = os.getenv("DEEPGRAM_MODEL", "nova")
    
    # OpenAI configuration

    # add openai api key to the config constant value
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "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")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    # LLM settings
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    LLM_MAX_TOKENS: Optional[int] = int(os.getenv("LLM_MAX_TOKENS")) if os.getenv("LLM_MAX_TOKENS") else None
    LLM_MAX_CONTEXT_MESSAGES: int = int(os.getenv("LLM_MAX_CONTEXT_MESSAGES", "20"))
    
    # System prompt
    LLM_SYSTEM_PROMPT: str = os.getenv(
        "LLM_SYSTEM_PROMPT",
        "You are a helpful voice assistant. Provide concise, natural responses suitable for voice interaction."
    )
    
    # ElevenLabs TTS configuration
    ELEVENLABS_API_KEY: Optional[str] = os.getenv("ELEVENLABS_API_KEY", "sk_55c31180bb9c89e7456a80e305e917dbdf73ac767cb26982")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "2zRM7PkgwBPiau2jvVXc")  # Rachel
    ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    
    # TTS voice settings
    TTS_STABILITY: float = float(os.getenv("TTS_STABILITY", "0.5"))
    TTS_SIMILARITY_BOOST: float = float(os.getenv("TTS_SIMILARITY_BOOST", "0.75"))
    TTS_STYLE: float = float(os.getenv("TTS_STYLE", "0.0"))
    TTS_USE_SPEAKER_BOOST: bool = os.getenv("TTS_USE_SPEAKER_BOOST", "true").lower() == "true"
    
    # Server configuration
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration."""
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        if not cls.ELEVENLABS_API_KEY:
            raise ValueError("ELEVENLABS_API_KEY environment variable is required")
        return True


config = Config()


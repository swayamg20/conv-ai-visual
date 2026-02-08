"""
Configuration module for voice AI application.
"""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class Config:
    """Application configuration."""
    # Provider selection
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")

    # Deepgram STT config
    DEEPGRAM_KEY: str = os.getenv("DEEPGRAM_KEY", "")
    DEEPGRAM_MODEL: str = os.getenv("DEEPGRAM_MODEL", "nova")
    # Endpointing: milliseconds of silence before finalizing transcript
    # With VAD endpoint detection enabled, this serves as a backup only
    # Default: 500ms when VAD enabled (was 1800ms)
    DEEPGRAM_ENDPOINTING: int = int(os.getenv("DEEPGRAM_ENDPOINTING", "500"))

    # VAD-based endpoint detection (replaces slow Deepgram endpointing)
    # Silero VAD detects speech end in ~300ms vs 1800ms Deepgram default
    VAD_ENDPOINT_ENABLED: bool = os.getenv("VAD_ENDPOINT_ENABLED", "true").lower() == "true"
    # Silence duration (ms) before triggering endpoint - lower = faster but may cut off
    VAD_SILENCE_THRESHOLD_MS: int = int(os.getenv("VAD_SILENCE_THRESHOLD_MS", "300"))
    # Minimum speech duration (ms) before considering endpoint - prevents false triggers
    VAD_MIN_SPEECH_MS: int = int(os.getenv("VAD_MIN_SPEECH_MS", "100"))
    # Minimum words in transcript to trigger endpoint
    VAD_MIN_WORDS: int = int(os.getenv("VAD_MIN_WORDS", "1"))
    # VAD speech probability threshold (0.0-1.0)
    VAD_THRESHOLD: float = float(os.getenv("VAD_THRESHOLD", "0.3"))

    # Sentence-level TTS streaming (reduces latency by starting TTS on first sentence)
    SENTENCE_STREAM_ENABLED: bool = os.getenv("SENTENCE_STREAM_ENABLED", "true").lower() == "true"
    # Minimum characters for a valid sentence (prevents TTS of fragments)
    MIN_SENTENCE_CHARS: int = int(os.getenv("MIN_SENTENCE_CHARS", "15"))

    # OpenAI config
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Gemini config (recommended for low latency)
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    # Gemini 2.0 Flash has faster TTFT (~200-400ms vs 500-800ms for GPT-4o-mini)
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")

    # Memory config
    MEM0_API_KEY: Optional[str] = os.getenv("MEM0_API_KEY")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    LLM_MAX_TOKENS: Optional[int] = int(os.getenv("LLM_MAX_TOKENS")) if os.getenv("LLM_MAX_TOKENS") else None
    # Reduced from 5 to 3 for faster context processing
    LLM_MAX_CONTEXT_MESSAGES: int = int(os.getenv("LLM_MAX_CONTEXT_MESSAGES", "3"))
    LLM_SYSTEM_PROMPT: str = os.getenv(
        "LLM_SYSTEM_PROMPT",
        "You are a helpful voice assistant. Provide concise, natural responses suitable for voice interaction."
    )
    
    LLM_CANVAS_SYSTEM_PROMPT: str = os.getenv(
        "LLM_CANVAS_SYSTEM_PROMPT",
        """You are an expert math and science teacher who explains concepts using animated visuals, like 3Blue1Brown.

You have a manim_animate tool available. Use it to create animated diagrams - DO NOT write code or show the tool call in your response.

RULES:
1. Use the manim_animate tool to draw animated diagrams (the user sees the animation, not the tool call)
2. Your text response should be brief and conversational (2-4 sentences)
3. Don't describe what you're drawing in detail - the animation speaks for itself
4. Never output code, function calls, or technical syntax in your spoken response
5. Build visuals step by step - add objects one at a time with animations between them

TEACHING STYLE:
- Start with the simplest visual, then build complexity
- Use "create" animation to draw shapes (the stroke appears progressively)
- Use "write" animation for equations
- Add brief pauses (0.3-0.5s) between steps so the student can follow
- Transform shapes to show relationships (e.g., circle → square)
- Use color to distinguish concepts: blue for primary, green for secondary, red for emphasis

YOUR RESPONSE should sound like a teacher narrating:
"Let me show you. See how the circle's area relates to pi times the radius squared..."

NEVER include manim_animate(), instructions, JSON, or any code in your response text."""
    )
    ELEVENLABS_API_KEY: Optional[str] = os.getenv("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel
    ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    TTS_STABILITY: float = float(os.getenv("TTS_STABILITY", "0.5"))
    TTS_SIMILARITY_BOOST: float = float(os.getenv("TTS_SIMILARITY_BOOST", "0.75"))
    TTS_STYLE: float = float(os.getenv("TTS_STYLE", "0.0"))
    TTS_USE_SPEAKER_BOOST: bool = os.getenv("TTS_USE_SPEAKER_BOOST", "true").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration based on provider."""
        provider = cls.LLM_PROVIDER.lower()

        # Validate LLM provider configuration
        if provider == "openai":
            if not cls.OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY environment variable is required for provider=openai")
        elif provider == "gemini":
            if not cls.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY environment variable is required for provider=gemini")
        else:
            raise ValueError(
                f"Unknown LLM_PROVIDER: {provider}. Supported providers: openai, gemini"
            )

        # Validate other required configuration
        if not cls.ELEVENLABS_API_KEY:
            raise ValueError("ELEVENLABS_API_KEY environment variable is required")

        return True


config = Config()
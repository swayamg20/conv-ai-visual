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
        """You are a senior systems architect teaching through voice and visual diagrams.

You have a canvas_update tool available. Use it to draw diagrams - DO NOT write code or show the tool call in your response.

RULES:
1. Use the canvas_update tool to draw diagrams (the user sees the drawing, not the tool call)
2. Your text response should be brief and conversational (2-4 sentences)
3. Don't describe what you're drawing - the diagram speaks for itself
4. Never output code, function calls, or technical syntax in your spoken response

CANVAS GUIDE:
- Canvas is 800x600, (0,0) is top-left
- Actions: rect, circle, ellipse, text, arrow, line
- Pass operations as JSON string to canvas_update tool

SIZING (CRITICAL - boxes must fit text):
- Minimum box width: 120px for short labels, 180px for medium, 250px for long labels
- Standard height: 50px
- Use SHORT labels: "LB" not "Load Balancer", "Gateway" not "API Gateway", "DB" not "Database"
- Text font_size: 16 (default)

COLORS:
- #3b82f6 (blue): Core services
- #10b981 (green): Caches, databases  
- #f59e0b (orange): Queues, async
- #ef4444 (red): Critical paths
- #6b7280 (gray): Infrastructure

LAYOUT for system design (center horizontally at x=400):
- Clients at top (y=50)
- LB/Gateway layer (y=150)
- Services row (y=300) - spread horizontally: x=150, x=400, x=650
- Data layer (y=450)

YOUR RESPONSE should sound like:
"Here's the architecture. Requests come through the load balancer, hit our API gateway, then route to the services."

NEVER include canvas_update(), Rectangle(), or any code in your response text."""
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
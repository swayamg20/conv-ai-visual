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

CRITICAL RULES:
1. DRAW THE COMPLETE DIAGRAM FIRST in a single canvas_update call with ALL components
2. Your spoken response should be CONVERSATIONAL - meant to be heard, not read
3. Don't describe what you're drawing in text - the diagram speaks for itself
4. Speak like you're explaining to a colleague: natural, flowing, no bullet points

RESPONSE FORMAT:
- First: Call canvas_update with the COMPLETE diagram (all components, arrows, labels at once)
- Then: Speak a brief conversational explanation (2-4 sentences max)
- Example spoken response: "Here's the architecture. Users hit our CDN first, then requests flow through the load balancer to our API gateway. The interesting part is how we handle writes - they go through a queue for better reliability."

VISUAL VOCABULARY:

Shapes:
- rectangle: Services, APIs (use rounded for external)
- ellipse: Databases, storage
- diamond: Decision points, routers
- text: Labels and annotations

Colors (semantic):
- #3b82f6 (blue): Core services, primary flow
- #10b981 (green): Caches, read replicas
- #f59e0b (orange): Async paths, queues
- #ef4444 (red): Critical paths, writes
- #8b5cf6 (purple): External services
- #6b7280 (gray): Infrastructure

LAYOUT (Canvas 800x600):

HLD Layout:
```
[Clients]           y=40
    ↓
[Load Balancer]     y=120
    ↓
[API Gateway]       y=200
    ↓
[Services Row]      y=300
    ↓
[Data Layer]        y=450
```

LLD Layout:
- Class boxes with name/attributes/methods sections
- Show relationships with labeled arrows
- Group by domain/module

COMPLETE DIAGRAM EXAMPLE for "Design Twitter":
Draw ALL of these in ONE canvas_update call:
- Client apps (x=400, y=40)
- CDN (x=400, y=100)
- Load Balancer (x=400, y=160)
- API Gateway (x=400, y=220)
- Tweet Service (x=200, y=320)
- Timeline Service (x=400, y=320)
- User Service (x=600, y=320)
- Redis Cache (x=200, y=450)
- PostgreSQL (x=400, y=450)
- Kafka Queue (x=600, y=450)
- All connecting arrows with protocol labels

SPOKEN RESPONSE STYLE:
✓ "Here's the high-level architecture. Traffic comes through our CDN and load balancer, then hits the API gateway which routes to three main services."
✓ "The key insight here is separating reads and writes. Reads go through cache, writes go through the queue."
✗ DON'T: "I'm drawing a rectangle for the user service..."
✗ DON'T: "Component 1: Load Balancer - handles traffic distribution..."
✗ DON'T: Long bullet-point explanations

Remember: Draw COMPLETE diagrams. Speak NATURALLY. The visual tells the story, your voice adds insight."""
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
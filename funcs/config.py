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
        """You are a brilliant math and science teacher in the style of 3Blue1Brown (Grant Sanderson). You make abstract concepts feel intuitive through beautiful animated visuals and clear narration.

You have a manim_animate tool. ALWAYS use it to create animations — the student sees the animation on a canvas beside your text. You never show code or tool calls.

PHILOSOPHY (channel 3Blue1Brown):
- Make the viewer FEEL the math, not just see it
- Build intuition before formulas — show the "why" visually first, equation last
- Every animation should reveal something — never animate just to animate
- Use geometric reasoning: show relationships through movement and transformation
- Surprise the viewer: transform one concept into another to reveal hidden connections

SCENE STRUCTURE (every explanation should follow this arc):
1. CLEAR the canvas if it's a new topic
2. TITLE — brief text at top, write it on
3. BUILD — construct the visual step by step (one object + one animation at a time)
4. REVEAL — show the key insight (transform, highlight, or annotate)
5. EQUATION — write the formula last, after the visual makes it obvious

COLOR PALETTE (3b1b style — ALWAYS use hex, NEVER named colors):
- #58C4DD — primary blue (main objects, default)
- #83C167 — green (secondary, comparisons)
- #FFFF00 — yellow (highlights, key insight)
- #FF862F — orange (annotations, labels)
- #FC6255 — red (emphasis, warnings, negation)
- #9A72AC — purple (tertiary, special)
- #FFFFFF — white (text, equations)
- #F0AC5F — gold (special highlights)

SPATIAL LAYOUT:
- Canvas: x ∈ [-7, 7], y ∈ [-4, 4]. Origin (0,0) = center.
- ALWAYS set "position" for every object
- Title: [0, 3.5, 0]  |  Main visuals: spread in center  |  Equation: [0, -3, 0]
- Side by side: x = -4, 0, 4  |  Vertical stack: y = 2, 0, -2
- NEVER overlap two objects at the same position

ANIMATION RULES:
- "create" for geometry (draws the stroke progressively — satisfying to watch)
- "write" for tex/text (reveals left to right)
- "fade_in" / "fade_out" for appearing/disappearing
- "transform" to morph one shape into another (powerful for showing relationships)
- Durations: 0.6–1.2s for shapes, 1.0–2.0s for equations, 0.3–0.5s waits between steps
- ALWAYS add a {"action": "wait", "duration": 0.3} between consecutive animations

CLEAR vs BUILD:
- New topic → start with {"action": "clear"}
- Continuing same topic → add to existing scene in unused positions
- When in doubt → clear and rebuild

YOUR TEXT RESPONSE (CRITICAL):
- Sound like a teacher narrating alongside the animation: conversational, curious, insightful
- 2-4 sentences max — the animation does the heavy lifting
- Point to what's happening: "Notice how...", "See that...", "Watch what happens when..."
- Express genuine wonder at the math: "And here's the beautiful part..."
- ABSOLUTELY NEVER include JSON, {"action":...} objects, function calls, code, or technical syntax in your text response
- ALL animation instructions go ONLY in the manim_animate tool call — NEVER in the text
- Your text response is ONLY natural language narration — no JSON, no code blocks, no instructions"""
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
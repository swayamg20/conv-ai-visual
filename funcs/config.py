"""
Configuration module for voice AI application.
"""
import os
from typing import Optional


class Config:
    """Application configuration."""
    DEEPGRAM_KEY: str = os.getenv("DEEPGRAM_KEY", "dea381e9d217d2451a3ef550b95b2735e58f101b")
    DEEPGRAM_MODEL: str = os.getenv("DEEPGRAM_MODEL", "nova")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "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")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    MEM0_API_KEY: Optional[str] = os.getenv("MEM0_API_KEY", "m0-HxU0GjNXPG2K2p5B6E3CkSiSD9L5v9lIcbhXCtrU")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    LLM_MAX_TOKENS: Optional[int] = int(os.getenv("LLM_MAX_TOKENS")) if os.getenv("LLM_MAX_TOKENS") else None
    LLM_MAX_CONTEXT_MESSAGES: int = int(os.getenv("LLM_MAX_CONTEXT_MESSAGES", "5"))
    LLM_SYSTEM_PROMPT: str = os.getenv(
        "LLM_SYSTEM_PROMPT",
        "You are a helpful voice assistant. Provide concise, natural responses suitable for voice interaction."
    )
    
    LLM_CANVAS_SYSTEM_PROMPT: str = os.getenv(
        "LLM_CANVAS_SYSTEM_PROMPT",
        """You are an interactive visual tutor. You ALWAYS use the canvas to illustrate your explanations.

CANVAS RULES:
- Draw diagrams, flowcharts, and visual aids for EVERY explanation
- Use clear colors: blue (#3b82f6) for main concepts, green (#10b981) for good/correct, red (#ef4444) for warnings/errors, orange (#f59e0b) for highlights
- Label important elements for reference
- Build diagrams incrementally as you explain
- Use arrows to show relationships and flow
- Position elements logically: left-to-right for sequences, top-to-bottom for hierarchies

TEACHING STYLE:
- Start with a visual overview, then explain while pointing to elements
- Use the canvas as a whiteboard - sketch, annotate, highlight
- Keep verbal explanations brief; let the visuals do the heavy lifting
- Reference drawn elements: "As you can see in the diagram..."

The canvas is 800x600. Coordinate (0,0) is top-left. Always use canvas_update for every response."""
    )
    ELEVENLABS_API_KEY: Optional[str] = os.getenv("ELEVENLABS_API_KEY", "sk_55c31180bb9c89e7456a80e305e917dbdf73ac767cb26982")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "2zRM7PkgwBPiau2jvVXc")  # Rachel
    ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    TTS_STABILITY: float = float(os.getenv("TTS_STABILITY", "0.5"))
    TTS_SIMILARITY_BOOST: float = float(os.getenv("TTS_SIMILARITY_BOOST", "0.75"))
    TTS_STYLE: float = float(os.getenv("TTS_STYLE", "0.0"))
    TTS_USE_SPEAKER_BOOST: bool = os.getenv("TTS_USE_SPEAKER_BOOST", "true").lower() == "true"
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
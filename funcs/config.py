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
    # Provider selection — Groq default for fast inference (~80ms TTFT)
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq")

    # Deepgram STT config
    DEEPGRAM_KEY: str = os.getenv("DEEPGRAM_KEY", "")
    DEEPGRAM_MODEL: str = os.getenv("DEEPGRAM_MODEL", "nova")
    # Endpointing: milliseconds of silence before finalizing transcript
    # Higher values = wait longer for user to continue speaking (better for natural pauses)
    # Lower values = faster finalization (but may cut off mid-sentence)
    # Recommended: 1500-2000ms for natural conversation with pauses
    # Default: 1800ms (1.8 seconds) - patient enough for natural speech
    DEEPGRAM_ENDPOINTING: int = int(os.getenv("DEEPGRAM_ENDPOINTING", "700"))
    # Utterance end: milliseconds of gap after last word before sending UtteranceEnd event
    # This works with endpointing to detect complete sentences vs just silence
    # Lower values = faster response but may cut mid-thought
    # Higher values = better sentence detection but slower
    # Default: 1000ms (1 second) - good balance for natural speech
    DEEPGRAM_UTTERANCE_END_MS: int = int(os.getenv("DEEPGRAM_UTTERANCE_END_MS", "1000"))

    # OpenAI config
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Groq config (OpenAI-compatible, ultra-fast inference)
    GROQ_API_KEY: Optional[str] = os.getenv("GROQ_API_KEY")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Gemini config
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

    # Web search (Tavily)
    TAVILY_API_KEY: Optional[str] = os.getenv("TAVILY_API_KEY")

    # Memory config
    MEM0_API_KEY: Optional[str] = os.getenv("MEM0_API_KEY")
    MEMORY_SEMANTIC_TIMEOUT_SECS: float = float(os.getenv("MEMORY_SEMANTIC_TIMEOUT_SECS", "1.0"))
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    LLM_MAX_TOKENS: Optional[int] = int(os.getenv("LLM_MAX_TOKENS")) if os.getenv("LLM_MAX_TOKENS") else None
    LLM_MAX_CONTEXT_MESSAGES: int = int(os.getenv("LLM_MAX_CONTEXT_MESSAGES", "5"))
    LLM_ASYNC_CONTEXT: bool = os.getenv("LLM_ASYNC_CONTEXT", "true").lower() == "true"
    LLM_TOOL_SCHEMA_CACHE: bool = os.getenv("LLM_TOOL_SCHEMA_CACHE", "true").lower() == "true"
    LLM_STREAM_TOOL_ORCHESTRATION: bool = os.getenv("LLM_STREAM_TOOL_ORCHESTRATION", "true").lower() == "true"
    LLM_PARALLEL_TOOLS: bool = os.getenv("LLM_PARALLEL_TOOLS", "true").lower() == "true"
    LLM_SYSTEM_PROMPT: str = os.getenv(
        "LLM_SYSTEM_PROMPT",
        "You are a helpful voice assistant. Provide concise, natural responses suitable for voice interaction."
    )
    
    LLM_CANVAS_SYSTEM_PROMPT: str = os.getenv(
        "LLM_CANVAS_SYSTEM_PROMPT",
        """You are a senior systems architect who teaches through visual diagrams. You explain system designs using the teach_with_visuals tool.

TOOL: teach_with_visuals
Use flowchart, tree, label, and equation components to build system diagrams step by step.

COMPONENTS FOR SYSTEM DESIGN:
- flowchart: { nodes: [{id, text}], edges: [[from, to]] } — services, pipelines, request flows
- tree: { root, children } — hierarchies, org charts, class inheritance
- equation: { latex } — formulas, complexity analysis (O(n log n))
- label: { text, fontSize, color } — annotations, protocol labels
- venn_diagram: { sets, intersectionLabel } — overlapping concepts
- bar_chart: { data, title } — performance comparisons

TEACHING APPROACH:
1. Start simple, add complexity incrementally
2. Each step narrates what's being shown
3. Use highlight to emphasize key components
4. Use clear: true when switching topics

EXAMPLE — "Design a URL shortener":
{
  "steps": [
    { "say": "Let me walk through the architecture of a URL shortener.", "clear": true, "show": { "component": "flowchart", "props": { "nodes": [{"id": "client", "text": "Client"}, {"id": "api", "text": "API Server"}, {"id": "db", "text": "Database"}, {"id": "cache", "text": "Redis Cache"}], "edges": [["client", "api"], ["api", "cache"], ["api", "db"]] }, "id": "arch" } },
    { "say": "The client sends a URL to shorten. The API server generates a short code.", "highlight": ["client", "api"] },
    { "say": "We check Redis first for fast lookups, falling back to the database.", "highlight": ["cache", "db"] }
  ]
}
"""
    )

    # Math Tutor System Prompt (SDL v2 — semantic components, no coordinates)
    LLM_MATH_TUTOR_PROMPT: str = os.getenv(
        "LLM_MATH_TUTOR_PROMPT",
        """You are a visual AI tutor. You explain concepts by combining spoken narration with visual components on an animated whiteboard.

TOOL: teach_with_visuals
Every explanation MUST use this tool. Each step has:
- "say": What you narrate aloud (conversational, clear)
- "show": A visual component to render (optional per step)
- "highlight": Element ID(s) to emphasize (optional)
- "clear": true to wipe the canvas (use for new topics)

COMPONENTS (use the simplest that fits):
- right_triangle: { sides: ["a", "b", "c"] }
- equation: { latex: "c^2 = a^2 + b^2" }
- coordinate_plane: { x_range: [-5, 5], y_range: [-5, 5], points: [{x: 2, y: 3, label: "P"}], grid: true }
- function_plot: { x_range: [-5, 5], y_range: [0, 25], points: [{x: -5, y: 25}, {x: -4, y: 16}, ..., {x: 5, y: 25}], color: "#38bdf8", grid: true }  — for plotting curves like y=x^2. Provide 15-25 evenly spaced points for smooth curves.
- number_line: { min: 0, max: 10, marks: [3, 7], highlight: 5 }
- bar_chart: { data: [{label: "A", value: 10}, {label: "B", value: 7}], title: "Scores" }
- flowchart: { nodes: [{id: "start", text: "Begin"}, {id: "process", text: "Do work"}], edges: [["start", "process"]] }
- tree: { root: "CEO", children: {"CEO": ["VP1", "VP2"], "VP1": ["Dev1", "Dev2"]} }
- venn_diagram: { sets: [{label: "Mammals"}, {label: "Pets"}], intersectionLabel: "Dogs" }
- circle_diagram: { radius_label: "r", diameter_label: "d" }
- label: { text: "Important!", fontSize: 20, color: "#ef4444" }

POSITION HINTS (optional — layout engine handles defaults):
- "center", "top", "bottom"
- { below: "triangle1" } — place below a previous component
- { rightOf: "equation1" } — place to the right

RULES:
1. First step of a new topic: set clear: true
2. Every step MUST have a "say" field — this IS your voice narration
3. Use "highlight" to draw attention to previously shown elements
4. Keep it visual: prefer components over long narration
5. Give components an "id" if you will reference them later (highlight, position below/rightOf)
6. Write "say" conversationally: "Let me show you..." not "The following demonstrates..."
7. Do NOT specify pixel coordinates — the layout engine handles positioning

EXAMPLE — "Explain the Pythagorean theorem":
{
  "steps": [
    { "say": "Let me show you the Pythagorean theorem with a right triangle.", "clear": true, "show": { "component": "right_triangle", "props": { "sides": ["a", "b", "c"] }, "id": "triangle1" } },
    { "say": "The key relationship is that c squared equals a squared plus b squared.", "show": { "component": "equation", "props": { "latex": "c^2 = a^2 + b^2" }, "position": { "below": "triangle1" }, "id": "eq1" } },
    { "say": "Where c is the hypotenuse, the longest side.", "highlight": "c" },
    { "say": "And a and b are the other two sides.", "highlight": ["a", "b"] }
  ]
}
"""
    )

    # Smart Turn detection config
    SMART_TURN_ENABLED: bool = os.getenv("SMART_TURN_ENABLED", "true").lower() == "true"
    SMART_TURN_THRESHOLD: float = float(os.getenv("SMART_TURN_THRESHOLD", "0.5"))
    SMART_TURN_STOP_SECS: float = float(os.getenv("SMART_TURN_STOP_SECS", "2.0"))
    SMART_TURN_MODEL_PATH: Optional[str] = os.getenv("SMART_TURN_MODEL_PATH")

    # TTS provider: "elevenlabs" (cloud, high quality) or "kokoro" (local ONNX, low latency)
    TTS_PROVIDER: str = os.getenv("TTS_PROVIDER", "elevenlabs")
    KOKORO_MODEL_PATH: Optional[str] = os.getenv("KOKORO_MODEL_PATH")

    ELEVENLABS_API_KEY: Optional[str] = os.getenv("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel
    ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    TTS_STABILITY: float = float(os.getenv("TTS_STABILITY", "0.5"))
    TTS_SIMILARITY_BOOST: float = float(os.getenv("TTS_SIMILARITY_BOOST", "0.75"))
    TTS_STYLE: float = float(os.getenv("TTS_STYLE", "0.0"))
    TTS_USE_SPEAKER_BOOST: bool = os.getenv("TTS_USE_SPEAKER_BOOST", "true").lower() == "true"
    # Auth / JWT
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 days

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
        elif provider == "groq":
            if not cls.GROQ_API_KEY:
                raise ValueError("GROQ_API_KEY environment variable is required for provider=groq")
        elif provider == "gemini":
            if not cls.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY environment variable is required for provider=gemini")
        else:
            raise ValueError(
                f"Unknown LLM_PROVIDER: {provider}. Supported providers: openai, groq, gemini"
            )

        # Validate TTS configuration
        if cls.TTS_PROVIDER == "elevenlabs" and not cls.ELEVENLABS_API_KEY:
            raise ValueError("ELEVENLABS_API_KEY environment variable is required when TTS_PROVIDER=elevenlabs")

        return True


config = Config()
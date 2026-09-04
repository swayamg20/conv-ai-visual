"""Environment-backed application configuration."""

import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from dotenv import load_dotenv

from murmur.live_scene.contracts import MAX_SCENE_MODEL_OUTPUT_TOKENS


def default_env_path() -> Path:
    """Resolve the documented project-level ``.env`` file."""
    source_root = Path(__file__).resolve().parents[3]
    if (source_root / "pyproject.toml").is_file():
        return source_root / ".env"
    return Path.cwd() / ".env"


if os.getenv("PYTHON_DOTENV_DISABLED") != "1":
    load_dotenv(dotenv_path=default_env_path())


def _parse_csv_env(value: str | None, default: tuple[str, ...]) -> list[str]:
    """Parse a comma-separated env var into a cleaned string list."""
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_azure_openai_endpoint(endpoint: str) -> str:
    """Normalize an Azure OpenAI resource endpoint to its OpenAI v1 base URL."""
    value = endpoint.strip()
    if not value:
        raise ValueError("AZURE_OPENAI_ENDPOINT must not be empty")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("AZURE_OPENAI_ENDPOINT must be a valid HTTPS URL") from exc

    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("AZURE_OPENAI_ENDPOINT must be a valid HTTPS URL")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise ValueError("AZURE_OPENAI_ENDPOINT must not contain credentials or a port")
    if parsed.query or parsed.fragment:
        raise ValueError("AZURE_OPENAI_ENDPOINT must not contain a query or fragment")

    hostname = parsed.hostname.lower()
    azure_suffixes = (".openai.azure.com", ".services.ai.azure.com")
    resource_name = next(
        (hostname[: -len(suffix)] for suffix in azure_suffixes if hostname.endswith(suffix)),
        "",
    )
    if (
        not resource_name
        or resource_name.startswith("-")
        or resource_name.endswith("-")
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in resource_name
        )
    ):
        raise ValueError("AZURE_OPENAI_ENDPOINT must use an Azure OpenAI resource hostname")

    path = parsed.path.rstrip("/")
    if path not in {"", "/openai/v1"}:
        raise ValueError(
            "AZURE_OPENAI_ENDPOINT must be the resource root or its /openai/v1 endpoint"
        )

    return f"https://{hostname}/openai/v1/"


def _provider_model(
    provider: str,
    *,
    openai_model: str,
    azure_openai_deployment: str,
    groq_model: str,
    gemini_model: str,
) -> str:
    """Resolve the configured model for one supported LLM provider."""
    return {
        "openai": openai_model,
        "azure_openai": azure_openai_deployment,
        "groq": groq_model,
        "gemini": gemini_model,
    }.get(provider.lower(), "")


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
    # Default: 700ms (0.7 seconds) - tuned here for faster turn finalization
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

    # Azure OpenAI v1 config. The deployment name is passed as the API's model value.
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv(
        "AZURE_OPENAI_DEPLOYMENT", "murmur-gpt-oss-120b"
    ).strip()

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
    LLM_MAX_TOKENS: Optional[int] = (
        int(os.getenv("LLM_MAX_TOKENS")) if os.getenv("LLM_MAX_TOKENS") else None
    )
    # Gate 1 live-scene authoring can be tuned independently while inheriting
    # the existing LLM selection when no scene-specific override is supplied.
    # Its bounded output budget must not inherit the unbounded chat setting:
    # scenes are default-off, and an otherwise valid chat budget must never
    # make application construction fail.
    MURMUR_SCENE_ENABLED: bool = os.getenv("MURMUR_SCENE_ENABLED", "false").lower() == "true"
    MURMUR_SCENE_LLM_PROVIDER: str = os.getenv("MURMUR_SCENE_LLM_PROVIDER") or LLM_PROVIDER
    MURMUR_SCENE_LLM_MODEL: str = os.getenv("MURMUR_SCENE_LLM_MODEL") or _provider_model(
        MURMUR_SCENE_LLM_PROVIDER,
        openai_model=OPENAI_MODEL,
        azure_openai_deployment=AZURE_OPENAI_DEPLOYMENT,
        groq_model=GROQ_MODEL,
        gemini_model=GEMINI_MODEL,
    )
    MURMUR_SCENE_LLM_MAX_TOKENS: int = int(
        os.getenv("MURMUR_SCENE_LLM_MAX_TOKENS") or MAX_SCENE_MODEL_OUTPUT_TOKENS
    )
    MURMUR_SCENE_LLM_TIMEOUT_SECONDS: float = float(
        os.getenv("MURMUR_SCENE_LLM_TIMEOUT_SECONDS", "20.0")
    )
    MURMUR_SCENE_LLM_TEMPERATURE: float = float(os.getenv("MURMUR_SCENE_LLM_TEMPERATURE", "0.2"))
    MURMUR_SCENE_GLOBAL_CONCURRENCY: int = int(os.getenv("MURMUR_SCENE_GLOBAL_CONCURRENCY", "4"))
    MURMUR_SCENE_PER_USER_CONCURRENCY: int = int(
        os.getenv("MURMUR_SCENE_PER_USER_CONCURRENCY", "1")
    )
    MURMUR_SCENE_REQUESTS_PER_MINUTE: int = int(os.getenv("MURMUR_SCENE_REQUESTS_PER_MINUTE", "10"))
    MURMUR_SCENE_PROVIDER_DISPATCHES_PER_MINUTE: int = int(
        os.getenv("MURMUR_SCENE_PROVIDER_DISPATCHES_PER_MINUTE", "10")
    )
    LLM_MAX_CONTEXT_MESSAGES: int = int(os.getenv("LLM_MAX_CONTEXT_MESSAGES", "20"))
    ALLOWED_CORS_ORIGINS: list[str] = _parse_csv_env(
        os.getenv("ALLOWED_CORS_ORIGINS"),
        ("http://localhost:3000",),
    )
    LLM_ASYNC_CONTEXT: bool = os.getenv("LLM_ASYNC_CONTEXT", "true").lower() == "true"
    LLM_TOOL_SCHEMA_CACHE: bool = os.getenv("LLM_TOOL_SCHEMA_CACHE", "true").lower() == "true"
    LLM_STREAM_TOOL_ORCHESTRATION: bool = (
        os.getenv("LLM_STREAM_TOOL_ORCHESTRATION", "true").lower() == "true"
    )
    LLM_PARALLEL_TOOLS: bool = os.getenv("LLM_PARALLEL_TOOLS", "true").lower() == "true"
    LLM_SYSTEM_PROMPT: str = os.getenv(
        "LLM_SYSTEM_PROMPT",
        "You are a helpful voice assistant. Provide concise, natural responses suitable for voice interaction.",
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
""",
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
""",
    )

    # Smart Turn detection config
    SMART_TURN_ENABLED: bool = os.getenv("SMART_TURN_ENABLED", "false").lower() == "true"
    SMART_TURN_THRESHOLD: float = float(os.getenv("SMART_TURN_THRESHOLD", "0.5"))
    SMART_TURN_STOP_SECS: float = float(os.getenv("SMART_TURN_STOP_SECS", "2.0"))
    SMART_TURN_MODEL_PATH: Optional[str] = os.getenv("SMART_TURN_MODEL_PATH")

    # Voice runtime selection and LiveKit V2 control-plane configuration.
    # Profile, worker, and signing policy are deliberately server-controlled.
    VOICE_RUNTIME: str = os.getenv("VOICE_RUNTIME", "legacy").lower()
    MURMUR_ENVIRONMENT: str = os.getenv("MURMUR_ENVIRONMENT", "development")
    LIVEKIT_URL: str = os.getenv("LIVEKIT_URL", "")
    LIVEKIT_API_KEY: str = os.getenv("LIVEKIT_API_KEY", "")
    LIVEKIT_API_SECRET: str = os.getenv("LIVEKIT_API_SECRET", "")
    VOICE_V2_SIGNING_SECRET: str = os.getenv("VOICE_V2_SIGNING_SECRET", "")
    VOICE_V2_PROFILE_ID: str = os.getenv("VOICE_V2_PROFILE_ID", "livekit-agents-cascade-v1")
    VOICE_V2_WORKER_NAME: str = os.getenv("VOICE_V2_WORKER_NAME", "murmur-voice-v2")
    # Keep optional Voice V2 numerics raw at application import time. The V2
    # factories parse and validate them only when that runtime is selected.
    VOICE_V2_TOKEN_TTL_SECONDS: str = os.getenv("VOICE_V2_TOKEN_TTL_SECONDS", "300")
    VOICE_V2_JOB_METADATA_TTL_SECONDS: str = os.getenv("VOICE_V2_JOB_METADATA_TTL_SECONDS", "300")
    VOICE_V2_JOB_METADATA_CLOCK_SKEW_SECONDS: str = os.getenv(
        "VOICE_V2_JOB_METADATA_CLOCK_SKEW_SECONDS", "30"
    )
    VOICE_V2_REPOSITORY_TIMEOUT_SECONDS: str = os.getenv("VOICE_V2_REPOSITORY_TIMEOUT_SECONDS", "2")
    VOICE_V2_PREFLIGHT_TIMEOUT_SECONDS: str = os.getenv("VOICE_V2_PREFLIGHT_TIMEOUT_SECONDS", "5")
    VOICE_V2_CONNECT_TIMEOUT_SECONDS: str = os.getenv("VOICE_V2_CONNECT_TIMEOUT_SECONDS", "10")
    VOICE_V2_PARTICIPANT_WAIT_TIMEOUT_SECONDS: str = os.getenv(
        "VOICE_V2_PARTICIPANT_WAIT_TIMEOUT_SECONDS", "15"
    )
    VOICE_V2_INPUT_WAIT_TIMEOUT_SECONDS: str = os.getenv(
        "VOICE_V2_INPUT_WAIT_TIMEOUT_SECONDS", "10"
    )
    VOICE_V2_SESSION_START_TIMEOUT_SECONDS: str = os.getenv(
        "VOICE_V2_SESSION_START_TIMEOUT_SECONDS", "10"
    )
    VOICE_V2_EVENT_PUBLISH_TIMEOUT_SECONDS: str = os.getenv(
        "VOICE_V2_EVENT_PUBLISH_TIMEOUT_SECONDS", "3"
    )
    # The named cascade profile fixes its provider/model/media choices in code.
    # Only the metadata-only reachability deadline is operator-tunable here.
    VOICE_V2_PROVIDER_PROBE_TIMEOUT_SECONDS: str = os.getenv(
        "VOICE_V2_PROVIDER_PROBE_TIMEOUT_SECONDS", "4"
    )
    VOICE_V2_ROOM_EMPTY_TIMEOUT_SECONDS: str = os.getenv(
        "VOICE_V2_ROOM_EMPTY_TIMEOUT_SECONDS", "60"
    )
    VOICE_V2_ROOM_DEPARTURE_TIMEOUT_SECONDS: str = os.getenv(
        "VOICE_V2_ROOM_DEPARTURE_TIMEOUT_SECONDS", "30"
    )
    VOICE_V2_CONTROL_PLANE_TIMEOUT_SECONDS: str = os.getenv(
        "VOICE_V2_CONTROL_PLANE_TIMEOUT_SECONDS", "5"
    )
    VOICE_V2_MAX_CONCURRENT_BOOTSTRAPS: str = os.getenv("VOICE_V2_MAX_CONCURRENT_BOOTSTRAPS", "100")
    VOICE_V2_MAX_ACTIVE_CALLS: str = os.getenv("VOICE_V2_MAX_ACTIVE_CALLS", "1")
    VOICE_V2_MAX_CALL_ASSIGNMENTS: str = os.getenv("VOICE_V2_MAX_CALL_ASSIGNMENTS", "10000")

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
    TTS_MAX_RETRIES: int = int(os.getenv("TTS_MAX_RETRIES", "2"))
    TTS_RETRY_BASE_DELAY_SECS: float = float(os.getenv("TTS_RETRY_BASE_DELAY_SECS", "0.35"))
    TTS_FALLBACK_TO_KOKORO: bool = os.getenv("TTS_FALLBACK_TO_KOKORO", "true").lower() == "true"
    # Firebase Auth
    FIREBASE_SERVICE_ACCOUNT_PATH: Optional[str] = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
    FIREBASE_PROJECT_ID: Optional[str] = os.getenv("FIREBASE_PROJECT_ID")

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration based on provider."""
        provider = cls.LLM_PROVIDER.lower()

        # Validate LLM provider configuration
        if provider == "openai":
            if not cls.OPENAI_API_KEY:
                raise ValueError(
                    "OPENAI_API_KEY environment variable is required for provider=openai"
                )
        elif provider == "azure_openai":
            if not cls.AZURE_OPENAI_API_KEY:
                raise ValueError(
                    "AZURE_OPENAI_API_KEY environment variable is required "
                    "for provider=azure_openai"
                )
            normalize_azure_openai_endpoint(cls.AZURE_OPENAI_ENDPOINT)
            if not cls.AZURE_OPENAI_DEPLOYMENT:
                raise ValueError(
                    "AZURE_OPENAI_DEPLOYMENT environment variable is required "
                    "for provider=azure_openai"
                )
        elif provider == "groq":
            if not cls.GROQ_API_KEY:
                raise ValueError("GROQ_API_KEY environment variable is required for provider=groq")
        elif provider == "gemini":
            if not cls.GEMINI_API_KEY:
                raise ValueError(
                    "GEMINI_API_KEY environment variable is required for provider=gemini"
                )
        else:
            raise ValueError(
                "Unknown LLM_PROVIDER: "
                f"{provider}. Supported providers: openai, azure_openai, groq, gemini"
            )

        scene_provider = cls.MURMUR_SCENE_LLM_PROVIDER.lower()
        if scene_provider not in {"openai", "azure_openai", "groq", "gemini"}:
            raise ValueError(
                "Unknown MURMUR_SCENE_LLM_PROVIDER: "
                f"{scene_provider}. Supported providers: openai, azure_openai, groq, gemini"
            )
        if cls.MURMUR_SCENE_ENABLED and scene_provider == "azure_openai":
            if not cls.AZURE_OPENAI_API_KEY:
                raise ValueError(
                    "AZURE_OPENAI_API_KEY environment variable is required "
                    "for MURMUR_SCENE_LLM_PROVIDER=azure_openai"
                )
            normalize_azure_openai_endpoint(cls.AZURE_OPENAI_ENDPOINT)
        if not cls.MURMUR_SCENE_LLM_MODEL:
            raise ValueError("MURMUR_SCENE_LLM_MODEL must not be empty")
        if not 1 <= cls.MURMUR_SCENE_LLM_MAX_TOKENS <= MAX_SCENE_MODEL_OUTPUT_TOKENS:
            raise ValueError(
                f"MURMUR_SCENE_LLM_MAX_TOKENS must be between 1 and {MAX_SCENE_MODEL_OUTPUT_TOKENS}"
            )
        if cls.MURMUR_SCENE_LLM_TIMEOUT_SECONDS <= 0:
            raise ValueError("MURMUR_SCENE_LLM_TIMEOUT_SECONDS must be greater than zero")
        if not 0 <= cls.MURMUR_SCENE_LLM_TEMPERATURE <= 2:
            raise ValueError("MURMUR_SCENE_LLM_TEMPERATURE must be between zero and two")
        if cls.MURMUR_SCENE_GLOBAL_CONCURRENCY <= 0:
            raise ValueError("MURMUR_SCENE_GLOBAL_CONCURRENCY must be greater than zero")
        if not 1 <= cls.MURMUR_SCENE_PER_USER_CONCURRENCY <= cls.MURMUR_SCENE_GLOBAL_CONCURRENCY:
            raise ValueError(
                "MURMUR_SCENE_PER_USER_CONCURRENCY must be between 1 and the global limit"
            )
        if cls.MURMUR_SCENE_REQUESTS_PER_MINUTE <= 0:
            raise ValueError("MURMUR_SCENE_REQUESTS_PER_MINUTE must be greater than zero")
        if cls.MURMUR_SCENE_PROVIDER_DISPATCHES_PER_MINUTE <= 0:
            raise ValueError(
                "MURMUR_SCENE_PROVIDER_DISPATCHES_PER_MINUTE must be greater than zero"
            )

        # Validate TTS configuration
        if cls.TTS_PROVIDER == "elevenlabs" and not cls.ELEVENLABS_API_KEY:
            raise ValueError(
                "ELEVENLABS_API_KEY environment variable is required when TTS_PROVIDER=elevenlabs"
            )

        if not cls.ALLOWED_CORS_ORIGINS:
            raise ValueError("ALLOWED_CORS_ORIGINS must contain at least one origin")

        return True


config = Config()

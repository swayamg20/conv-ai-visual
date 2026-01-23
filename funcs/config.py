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
    # Higher values = wait longer for user to continue speaking (better for natural pauses)
    # Lower values = faster finalization (but may cut off mid-sentence)
    # Recommended: 1500-2000ms for natural conversation with pauses
    # Default: 1800ms (1.8 seconds) - patient enough for natural speech
    DEEPGRAM_ENDPOINTING: int = int(os.getenv("DEEPGRAM_ENDPOINTING", "1800"))
    # Utterance end: milliseconds of gap after last word before sending UtteranceEnd event
    # This works with endpointing to detect complete sentences vs just silence
    # Lower values = faster response but may cut mid-thought
    # Higher values = better sentence detection but slower
    # Default: 1000ms (1 second) - good balance for natural speech
    DEEPGRAM_UTTERANCE_END_MS: int = int(os.getenv("DEEPGRAM_UTTERANCE_END_MS", "1000"))

    # OpenAI config
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Gemini config
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

    # Memory config
    MEM0_API_KEY: Optional[str] = os.getenv("MEM0_API_KEY")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    LLM_MAX_TOKENS: Optional[int] = int(os.getenv("LLM_MAX_TOKENS")) if os.getenv("LLM_MAX_TOKENS") else None
    LLM_MAX_CONTEXT_MESSAGES: int = int(os.getenv("LLM_MAX_CONTEXT_MESSAGES", "5"))
    LLM_SYSTEM_PROMPT: str = os.getenv(
        "LLM_SYSTEM_PROMPT",
        "You are a helpful voice assistant. Provide concise, natural responses suitable for voice interaction."
    )
    
    LLM_CANVAS_SYSTEM_PROMPT: str = os.getenv(
        "LLM_CANVAS_SYSTEM_PROMPT",
        """
        You are a senior systems architect who teaches through visual diagrams. You ALWAYS use the canvas to illustrate system designs.

DOMAIN: System Design, HLD, LLD, Backend Architecture

VISUAL VOCABULARY - Use these consistently:

Components:
- Rectangles: Services, APIs, Applications (rounded corners for external services)
- Cylinders: Databases, persistent storage
- Parallelograms: Message queues, event streams
- Clouds: External services, third-party APIs, CDNs
- Hexagons: Load balancers, API gateways
- Circles/Pills: Clients (mobile, web, IoT)

Colors (semantic meaning):
- #3b82f6 (blue): Core services, primary data flow
- #10b981 (green): Caches, read replicas, optimizations
- #f59e0b (orange): Async paths, queues, background jobs
- #ef4444 (red): Write paths, critical data, single points of failure
- #8b5cf6 (purple): External/third-party services
- #6b7280 (gray): Supporting infrastructure (logging, monitoring)

Arrows & Connections:
- Solid arrows: Synchronous calls (HTTP, gRPC)
- Dashed arrows: Async communication (events, queues)
- Thick arrows: High-throughput paths
- Bidirectional: Two-way communication

LAYOUT PATTERNS:

For HLD (High-Level Design):
- Top: Clients/Users
- Middle: Load Balancers → API Gateway → Services
- Bottom: Data layer (DBs, caches, queues)
- Left-to-right: Request flow
- Group related services visually

For LLD (Low-Level Design):
- Show internal class/module structure
- Use compartmentalized boxes (class name | attributes | methods)
- Show inheritance with hollow arrows, composition with filled diamonds
- Include interface boundaries

For Data Flow:
- Left: Source/Input
- Right: Sink/Output  
- Show transformations in between
- Label data formats at boundaries (JSON, Protobuf, etc.)

ANNOTATION STANDARDS:
- Label every component with its name
- Add protocol labels on arrows (REST, gRPC, WebSocket, Kafka)
- Include latency estimates on critical paths (p99: ~50ms)
- Mark read/write ratios where relevant (R:W = 100:1)
- Show data sizes for storage components
- Add replica counts (x3) for distributed components

TEACHING APPROACH:
1. Start with the simplest version that solves the core problem
2. Incrementally add: caching → async processing → replication → sharding
3. Explain trade-offs visually (add annotations for pros/cons)
4. Highlight bottlenecks and single points of failure
5. Show before/after for optimizations

COMMON PATTERNS TO DRAW:
- Request flow: Client → CDN → LB → Gateway → Service → Cache → DB
- Write-behind: Service → Queue → Worker → DB
- CQRS: Separate read/write paths visually
- Event sourcing: Show event log as central cylinder
- Microservices: Bounded boxes with clear API boundaries
- Database patterns: Primary-replica, sharding with partition keys

SCALE INDICATORS:
- Add QPS/RPS estimates near services
- Show data volume near storage
- Indicate horizontal scaling with stacked rectangles + "×N"
- Mark stateless (can scale) vs stateful (needs coordination)

Canvas is 800x600. Origin (0,0) is top-left. 
Standard margins: 40px from edges.
Component spacing: 80-120px between layers, 60px within layers.

ALWAYS use canvas_update. Build diagrams incrementally as you explain each component.
        """
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
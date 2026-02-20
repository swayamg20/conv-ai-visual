## Done

- Chat (text sessions)
- STT → VAD → LLM → TTS pipeline
- Tool calling (sandboxed execution)
- Memory (mem0ai)
- SQLite DB (tools, logs, sessions)
- Canvas API (Rough.js + GSAP animations)
- WebRTC integration
- Interruption handling (server-side VAD)
- Smart Turn Detection (pipecat-ai/smart-turn-v3)
- Multi-provider LLM (OpenAI, Gemini, Groq)
- Sentence-pipelined TTS
- Observability dashboard

## In Progress

- Canvas animations — diagrams improving, Rough.js + GSAP pipeline working
- Explore + discuss + experiment with canvas (not just pre-built topic explanations)

## Next Up

### Canvas & Visual (high priority)
- Agent finds and places images on canvas in real-time while explaining
- https://jsoncanvas.org/ for canvas data format
- Fillers — low latency visual/audio fillers while LLM thinks

### LLM & Routing
- Re-routing LLM calls based on complexity for better latency
- Try different LLMs/SLMs/classifiers per task

### Tools & Execution
- Sandbox coding tools — user can write + execute code live on canvas
- Vector search scope discovery

### Lower Priority
- Redis (caching, pub/sub)
- Live web search
- Calendar/events integration
- Quartz-based documentation site

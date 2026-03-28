# Product Manager Agent Memory

## Core Vision (Founder-defined, March 2026)
- Platform where users create personalized AI agents via conversational canvas
- Three core capabilities: Background Agent, Web Search, Sandbox
- Agent = Prompt + Resources + Memory (simple mental model)
- Users describe themselves, system builds the agent (no prompt engineering)
- Voice-first, canvas-enhanced interaction
- Cross-session memory is critical
- Export as summarized PDF (not raw transcripts), share capability later
- Assessment mode: load question banks (JEE papers etc), quiz users
- Education primary vertical (CBSE/JEE), technical exploration secondary (system design)
- Multiple agents per user, each for different purposes

## Product Evolution Timeline
- **v0 (Dec 2024-Jan 2025)**: Basic WebRTC + STT + LLM + TTS pipeline, tool calling
- **v1 (Jan-Feb 2025)**: Canvas (Rough.js + GSAP), interruption, smart turn, memory, observability dashboard
- **v1.5 (Feb 2025)**: Scene Kit SDL, latency optimization (Groq, Kokoro TTS), model routing
- **v2 vision (Mar 2025)**: Personalized agent platform with canvas (CURRENT DIRECTION)

## What's Built (Solid Foundation)
- Real-time voice pipeline: WebRTC -> Deepgram STT -> Smart Turn -> LLM -> TTS -> audio back
- Canvas: Rough.js hand-drawn style + GSAP animations, Scene Kit with 10+ components
- Tool calling with RestrictedPython sandbox
- Multi-provider LLM (OpenAI, Gemini, Groq) with model routing
- Dual TTS (ElevenLabs cloud, Kokoro local)
- 4-layer memory (context, episodic, semantic/Mem0, user profile)
- Interruption handling (~70-130ms latency)
- Chat (SSE) + Voice (WebRTC) modes
- Observability dashboard with LLM call logs
- ~13,250 LOC, ~$97K engineering value

## What's Actually Built (Verified March 2026 — post feature sprint)
- Auth (JWT), Agent CRUD, creation wizard, landing page
- Resource ingestion: PDF (pymupdf) + URL (httpx+BS4), chunked, keyword search
- Web search (Tavily), wired as DB tool
- Struggle heatmap: topic_mastery table + TopicMasteryRepo aggregation
- Cross-session memory: agent_id scoped, session summaries, message persistence

## Core Engineering Phase (March 2026) — Quality-First Pivot
- Full analysis at: agent-memory/product-manager/core-engineering-priorities.md
- **Biggest AI/ML gap**: Resource search is keyword-only (SQLite LIKE). Need embeddings.
- **Possible pipeline bug**: LLMPipeline.__init__() may not pass agent_id to MemoryManager. Verify.
- **Context window too short**: LLM_MAX_CONTEXT_MESSAGES defaults to 5. Should be 20 minimum for tutoring.
- **Model router too naive**: Regex patterns only. Misses physics/math/SDL complexity signals.
- **One mind-blowing ML feature**: Adaptive difficulty — detect confusion from voice/correction patterns, auto-reduce complexity.

## Design Language — "Murmur"
- Blackboard aesthetic, not dashboard. Chalk on dark surface.
- Rough.js hand-drawn shapes, GSAP animations, "drawn not placed"
- Colors: void (#08080C), chalk (#E8E4DC), amber (#F5A623), lavender (#8B7EC8), sage (#6BCB77)
- Glass panels with backdrop blur, grain texture
- Voice orb as the product's face (states: idle, connecting, listening, thinking, speaking)
- Light mode also added (feat/light-mode branch)

## Decision Intelligence (Longer-term Vision)
- Behavioral preference extraction over declared preferences
- Decision state schema: stage, confidence, risk tolerance, hesitation markers
- Domain-agnostic (travel -> education -> anything)
- Fine-tuned SLM (3B-7B) for decision state extraction
- North star: reduction in user uncertainty per interaction
- NOT a priority for v2 agent platform launch but foundational thinking

## Document Freshness Assessment
- **VISION.md**: CURRENT - core philosophy still valid, mentions multiplayer as long-term
- **ARCHITECTURE_V2.md**: CURRENT - SDL/Scene Kit architecture, Phase 1-2 done, Phase 3-4 pending
- **DESIGN_LANGUAGE.md**: CURRENT - "Murmur" design system, still the reference
- **next_steps.md**: PARTIALLY OUTDATED - "In Progress" items may be done, priorities shifting to agent platform
- **decision_intelligence.md / ARTICLE**: CURRENT but DEFERRED - valid thinking, not immediate priority
- **INTERRUPTION_ARCHITECTURE.md**: OUTDATED - replaced by simpler approach in REFACTORING_SUMMARY.md
- **CLIENT_CHANGES_SUMMARY.md**: OUTDATED - describes old interruption client, superseded by refactoring
- **INTERRUPTION_FLOW.md**: CURRENT - describes the simplified interruption flow post-refactoring
- **REFACTORING_SUMMARY.md**: CURRENT - documents simplification (removed VAD, simplified interruption)
- **LLM_INTEGRATION.md**: PARTIALLY OUTDATED - basic flow still valid but doesn't cover multi-provider, SDL
- **TTS_INTEGRATION.md**: PARTIALLY OUTDATED - ElevenLabs info valid but doesn't mention Kokoro
- **TOOLS.md**: CURRENT - tool system reference still accurate
- **DATABASE.md**: CURRENT - schema reference still accurate
- **SETUP.md**: MOSTLY CURRENT - setup flow valid, some details may lag
- **COST_ESTIMATION.md**: CURRENT (Mar 2026) - codebase valuation
- **logo.md**: CURRENT - brand/logo prompt reference

## Key Product Principles (from docs + founder)
1. Voice-first, canvas-enhanced — voice is interface, canvas is workspace
2. Latency is everything — <1s simple, <2s visual explanations
3. Tools are first-class — AI acts, not just talks
4. The canvas is not a gimmick — primary output surface
5. Observation over declaration — behavioral data > stated preferences
6. Scene Compiler is the moat — not the LLM
7. "Drawn, not placed" — hand-drawn aesthetic is identity
8. Simple > complex — refactoring removed VAD, simplified interruption. Start simple, add complexity only when needed.

## Architecture Moat
- Scene Kit SDL: LLM generates ~80 tokens, compiler produces canvas. Anyone can plug LLM into canvas, but the Scene Compiler + Component Library is the moat.
- 4-layer memory system differentiates from stateless chatbots.
- For v2: Agent creation + resource ingestion + cross-session memory = compound moat.

## v2 Agent Platform — Key PM Decisions (March 2026)
- Full analysis at: agent-memory/product-manager/v2-agent-platform-analysis.md
- **Recommended MVP**: Template Gallery (5-10 pre-built agents) BEFORE custom creation wizard
- **Hardest problem**: Resource ingestion (RAG quality is make-or-break)
- **Hard prerequisite**: Auth (can't scope memory per agent without identity)
- **Do NOT build in MVP**: Assessment, PDF export, background agent, share, PDF upload
- **Build order**: Auth -> Agent data model -> Templates/Creation wizard -> Memory per agent -> URL ingestion -> Session launcher
- **Key open question**: Is resource ingestion actually needed for MVP, or is a well-prompted agent + web search good enough?
- **Memory system change needed**: Existing memory is per user_id only. Must add agent_id scope to EpisodicMemory, SemanticMemory (Mem0 supports agent_id param), UserProfile.
- **Full MVP spec**: agent-memory/product-manager/mvp-spec.md — canonical build document
- **MVP decision (March 2026)**: NO creation wizard for first 10 users. Founder hand-configs agents via admin API. Templates-first is confirmed approach.

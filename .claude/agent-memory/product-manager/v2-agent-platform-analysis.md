# v2 Agent Platform — PM Analysis
## Date: March 2026

## Vision Assessment

### What's Strong
1. Vertical-agnostic platform with a simple mental model (Agent = Prompt + Resources + Memory)
2. The canvas + voice combination is genuinely differentiated — nobody has nailed this
3. Education vertical is high-signal: CBSE/JEE is a massive, underserved, high-intent market in India
4. Founder insight that "verticals are just different agents" is correct — it prevents premature specialization
5. The foundation (voice pipeline + canvas + Scene Kit + memory layers) is more built than most startups have

### What's Weak / Risky
1. **Resource ingestion is the hardest problem** — loading NCERT PDFs, chunking, embedding, retrieval is a real pipeline. Getting quality RAG on domain content is non-trivial. This is 3-4 weeks of backend work minimum.
2. **No auth / user accounts** — cross-session memory needs identity. Without this, "agent remembers progress" is impossible. This is a hard prerequisite, not a nice-to-have.
3. **Assessment mode is a separate product feature** — it requires quiz engine logic, answer evaluation, scoring. Don't conflate it with the agent platform.
4. **PDF export is also a separate product feature** — canvas snapshots + summarization requires its own pipeline.
5. **Background agent is vague** — "autonomous resource gathering" is undefined. What triggers it? What does it do? This needs much clearer scope before building.
6. **Web search** — doable (Tavily/Serper API), but needs to be grounded in agent context to be useful. Not just "search the web" but "search within the agent's domain."
7. **The agent creation wizard is UX-heavy** — collecting user description, generating prompt, presenting resources for confirmation is a multi-step flow. Needs careful design.

## What Should the MVP Be?

### The Core Hypothesis to Test
"A user can describe themselves, get a configured AI tutor/expert, and have a useful voice+canvas session — and come back the next day and the agent remembers them."

That's the hypothesis. Test that before building assessment, export, background agent, or sharing.

### MVP Scope (4-6 weeks for solo dev)

**Phase 1: Agent Creation + Basic Session (Week 1-2)**
- User auth: minimal. Email + password or Google OAuth. Must exist before cross-session memory.
- Agent creation flow: text input ("I'm a 7th class CBSE physics student") -> system generates system prompt -> agent saved to DB
- Multiple agents per user (simple list)
- Session launches with the agent's prompt (what already works, just scoped to an agent)

**Phase 2: Cross-Session Memory Per Agent (Week 2-3)**
- Existing 4-layer memory system already works — just needs to be scoped per agent_id, not just user_id
- EpisodicMemory, SemanticMemory, UserProfile all need agent_id as a scope key
- Session history per agent (what topics covered, what the user struggled with)

**Phase 3: Resource Ingestion — Minimal Version (Week 3-5)**
- Start with URL ingestion only (no PDF upload in MVP — too complex)
- System suggests 3-5 relevant URLs/resources when agent is created
- User confirms which to include
- Simple scrape + chunk + embed pipeline (LlamaIndex or direct embeddings)
- Vector search at session time to inject relevant content into context
- PDF ingestion is v1.1 — don't build it now

**Out of MVP Scope (explicitly)**
- Assessment/quiz mode
- PDF export
- Background agent (autonomous tasks)
- Share functionality
- Multiple resource types (only URLs in MVP)
- PDF upload

## Build Order

1. **Auth** — blocks everything. Can't have cross-session memory without identity.
2. **Agent data model** — Agent entity with prompt, config, owner. Simple CRUD.
3. **Agent creation wizard** — UI for describing yourself, prompt generation, agent saved.
4. **Scope memory per agent** — existing memory system, add agent_id dimension.
5. **URL resource ingestion** — basic RAG pipeline on scraped content.
6. **Session launcher** — pick an agent, start a voice+canvas session with that agent's context.
7. **Session history view** — simple list of past sessions per agent.

## Biggest Risks

1. **RAG quality** — If the injected resource content makes the agent worse or irrelevant, the whole value prop breaks. Need to be rigorous about chunking, context window management, and relevance scoring.
2. **Agent creation UX** — The wizard needs to feel magical, not like filling out a form. The hardest part is generating a prompt that's actually good from a vague user description. Need serious prompt engineering on the meta-prompt.
3. **Auth scope creep** — Don't build a full user management system. Just enough auth to scope data. Use Clerk or NextAuth to keep it to 1 day of work.
4. **Memory layer complexity** — The existing memory system is per user_id. Adding agent_id as a scope means touching EpisodicMemoryModel, SemanticMemory (Mem0 agent_id param), and potentially all prompt injection. This is mechanical but needs careful execution.
5. **"Personalization" over-promise** — Don't promise the agent is deeply personalized if the prompt generation is basic. Under-promise, over-deliver.

## Assumptions to Validate Before Going Deep

1. **Do users actually want to create agents, or do they want a pre-built tutor?** — An "I'm a CBSE 7th class student" agent is almost the same for all such students. Pre-built templates might get you to value faster than the creation wizard.
2. **Is resource ingestion actually necessary for MVP?** — A well-prompted agent with web search might be "good enough" for first sessions. Don't invest in RAG until you know sessions without it are insufficient.
3. **What's the session completion rate?** — If users have one voice session and never come back, cross-session memory doesn't matter. Retention is the real test.

## Pre-Built Templates as a Faster MVP

**Alternative MVP: Template Gallery**
- Ship 5-10 pre-built agents (CBSE 7th Physics, CBSE 10th Math, JEE Prep, System Design, etc.)
- User picks one, optionally customizes name/preferences
- Immediately into a voice+canvas session
- Sessions tracked per user per agent-template

This is 60% less work than the full creation wizard and gets you learning about what users actually do in sessions. The "custom agent creation" becomes v1.1 after you understand usage patterns.

This is probably the right MVP. Ship templates first, creation wizard second.

## Open Questions for Founder

1. Who is the first real user? A CBSE student you know personally? Start there.
2. Do you have access to NCERT PDFs legally? Resource ingestion needs content.
3. Auth: are you okay with Google OAuth only to start, or do you need email/password?
4. What's your hosting plan? SQLite + FastAPI works for solo but won't scale. When do you add Postgres?
5. For the meta-prompt (generating agent prompts from user descriptions) — do you want to invest in that now or hardcode good prompts for the initial 5-10 templates?

# Core Engineering & AI/ML Priorities
## Date: March 2026
## Context: Feature build complete, founder shifting to quality-first mode

## What's Actually Built (Verified Against Codebase)
- JWT auth + agents CRUD: YES (funcs/auth.py, funcs/agents.py, models have AgentModel)
- Cross-session memory: YES — agent_id scope in MemoryManager, session summaries in SessionRepo
- Resource ingestion: YES — PDF (pymupdf) + URL (httpx+BS4), chunked, keyword search via ResourceChunkRepo.search()
- Web search: YES — Tavily wired as a DB tool, registered on startup
- Struggle heatmap: YES — topic_mastery table, TopicMasteryRepo with aggregation
- Message persistence: YES — ConversationMessageRepo.save() called via persist_message()
- Smart Turn v3: YES — ONNX model (~8M params), <60ms, singleton

## Critical Gap Found: Resource Search is KEYWORD-ONLY
- search_chunks() in resources.py uses ResourceChunkRepo.search() — pure SQLite LIKE query
- No embeddings, no vector search. This is the biggest AI/ML quality gap.
- The LLM calls search_resources tool with a semantic query but gets keyword matches back.
- On NCERT content: "explain work-energy theorem" won't find a chunk titled "8.3 The Work-Energy Theorem"

## Critical Gap Found: Model Router is Regex-Only
- model_router.py routes on regex patterns ("prove", "derive", "theorem")
- Missing: math/science specific escalation, canvas SDL complexity, streaming tool calls
- No routing logic for when to use Groq vs GPT-4o for canvas generation quality

## Critical Gap Found: agent_id NOT passed to LLMPipeline
- LLMPipeline.__init__() does NOT have agent_id param (verified in code)
- MemoryManager has agent_id but pipeline doesn't pass it through
- Memory IS scoped in MemoryManager but the pipeline constructor drops it
- The memory system may be storing/retrieving without proper agent scope

## Critical Gap Found: MAX_CONTEXT_MESSAGES = 5 (very low)
- config.py: LLM_MAX_CONTEXT_MESSAGES defaults to 5
- For a physics tutoring session, 5 messages means agent forgets earlier in the SAME session
- Students asking follow-up questions will confuse the agent

## Struggle Heatmap: Exists in DB but extraction mechanism unclear
- TopicMasteryRepo exists, aggregation query exists
- How mastery signals are extracted from conversation unclear — not found in pipeline integration

## Priority Recommendations (March 2026)
See main response in conversation for full ranked list.

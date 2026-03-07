# Voice AI — Launch Status

**Status: PRE-LAUNCH**
**Last updated:** 2026-03-07

---

## Current State

Voice AI has **not launched**. The product is in active development with core infrastructure built but key user-facing features incomplete.

## What's Built (Internal/Dev Ready)

| Component | Status | Notes |
|-----------|--------|-------|
| Voice pipeline (WebRTC + STT + TTS) | Done | Deepgram STT, ElevenLabs TTS, sentence-pipelined |
| Smart Turn Detection | Done | Silero VAD + pipecat-ai/smart-turn-v3 |
| Interruption handling | Done | Server-side VAD, cancels TTS mid-stream |
| Canvas (Rough.js + GSAP) | Done | Hand-drawn style, animated sequences |
| SDL + Scene Compiler | Done | Deterministic layout, ~80 token LLM output |
| Tool calling (sandboxed) | Done | RestrictedPython execution |
| Chat interface (SSE streaming) | Done | Text sessions with streaming |
| Multi-provider LLM | Done | OpenAI, Gemini, Groq |
| Observability dashboard | Done | LLM call logs, latency stats |
| Latency optimization | Done | Groq default, Kokoro local TTS |

## What's Missing for Launch

| Feature | Priority | Status | Blocks Launch? |
|---------|----------|--------|----------------|
| Agent creation/management | P0 | Not started | Yes |
| User accounts / auth | P0 | Not started | Yes |
| Cross-session memory | P0 | Not started | Yes |
| Resource ingestion (PDFs, web) | P1 | Not started | Yes |
| Web search integration | P1 | Not started | Partial |
| Visual polish (Phase 3) | P1 | Not started | Yes |
| Hardening (Phase 4) | P1 | Not started | Yes |
| Export / PDF generation | P2 | Not started | No |
| Sandbox code execution | P2 | Not started | No |
| Assessment/quiz mode | P2 | Not started | No |

## Launch Criteria

Before any public launch (even limited beta), these must be true:

1. **Users can create an account and sign in**
2. **Users can create at least one personalized agent** (describe themselves, system builds the agent)
3. **Sessions persist across visits** (cross-session memory works)
4. **Core voice + canvas experience is polished** (no jarring visual bugs, reasonable latency)
5. **Error handling is solid** (graceful degradation, no crashes on bad input)
6. **Basic resource ingestion works** (at minimum: web URLs, ideally PDFs)

## Launch Plan

| Phase | Target | Scope |
|-------|--------|-------|
| Alpha | TBD | Internal testing, founder + close friends |
| Closed Beta | TBD | 10-20 education-focused users (CBSE/JEE students) |
| Open Beta | TBD | Public sign-up with waitlist |
| GA | TBD | General availability |

## Risks & Blockers

- **No auth system** — cannot onboard real users without accounts
- **No agent persistence** — the core product concept (personalized agents) doesn't exist yet
- **Solo founder** — bandwidth is the primary constraint; must ruthlessly prioritize
- **Latency sensitivity** — voice products have zero tolerance for lag; needs real-user testing

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03 | Focus on agent creation system as P0 | Core product differentiator; everything else is infrastructure without it |

---

*This document is the source of truth for launch readiness. All agents (CEO, CTO, PM) should reference this before making roadmap or prioritization decisions.*

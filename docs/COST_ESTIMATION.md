# Codebase Cost Estimation Report

**Project**: Voice AI (`voiceai`)
**Date**: 2026-03-05
**Analyzed by**: Claude (Codebase Cost Estimator)

---

## 1. Codebase Overview


| Category       | Files  | Lines of Code | Primary Languages / Frameworks         |
| -------------- | ------ | ------------- | -------------------------------------- |
| Backend Core   | 17     | ~5,300        | Python, FastAPI, aiortc, asyncio       |
| Frontend UI    | 22     | ~4,700        | TypeScript, React, Next.js, Tailwind   |
| Frontend Hooks | 4      | ~1,050        | TypeScript (WebRTC, VAD, Audio, Chat)  |
| Frontend Lib   | 14     | ~1,550        | TypeScript (GSAP, Rough.js, Scene Kit) |
| Tests          | 4      | ~350          | Python (pytest-style)                  |
| Config / Infra | 8      | ~300          | JSON, YAML, TOML, MJS                  |
| Docs           | 20     | ~5,130        | Markdown (not counted in dev hours)    |
| **TOTAL**      | **89** | **~13,250**   |                                        |


> Note: `node_modules`, `__pycache__`, `.next`, `venv`, lock files, and audio/model binary files excluded.

### Tech Stack

- **Languages**: Python 3.11, TypeScript 5
- **Frameworks**: FastAPI, Next.js 15 (App Router), React 18, Tailwind CSS
- **AI / ML**: OpenAI GPT-4o-mini, Google Gemini, ONNX Runtime (Smart Turn v3, Whisper Tiny encoder), Mem0 (vector memory), Kokoro local TTS
- **Real-Time**: aiortc (WebRTC), Deepgram STT (WebSocket streaming), ElevenLabs TTS, Silero VAD
- **Canvas / Viz**: Rough.js, GSAP, KaTeX (math rendering), custom Scene Kit SDL
- **Database**: SQLite via SQLModel
- **Infra**: Uvicorn, CORS middleware, SSE streaming

---

## 2. Component Complexity Breakdown


| Component                            | LOC         | Tier | Complexity Label | Est. Hours |
| ------------------------------------ | ----------- | ---- | ---------------- | ---------- |
| WebRTC + Audio Pipeline              | ~2,400      | 4    | Advanced         | 160        |
| LLM Pipeline + Multi-Provider Client | ~2,012      | 4    | Advanced         | 155        |
| Canvas + Animation System            | ~2,100      | 4    | Advanced         | 150        |
| Smart Turn Detection (ONNX/ML)       | ~404        | 4    | Advanced         | 65         |
| Memory System (4-layer architecture) | ~1,113      | 3    | Complex          | 55         |
| Scene Kit (Visual Component Library) | ~1,300      | 3    | Complex          | 52         |
| Tool System + Sandbox Executor       | ~750        | 3    | Complex          | 35         |
| TTS Pipeline (ElevenLabs + Kokoro)   | ~164        | 3    | Complex          | 20         |
| VAD + Interruption Detection         | ~210        | 3    | Complex          | 20         |
| Dashboard (Analytics UI)             | ~598        | 2    | Standard         | 20         |
| Frontend UI Components               | ~900        | 2    | Standard         | 25         |
| Auth + Config                        | ~231        | 1    | Simple           | 10         |
| Infra / Config files                 | ~300        | 1    | Simple           | 8          |
| Tests (4 test files)                 | ~350        | —    | (included above) | —          |
| **TOTAL**                            | **~12,532** |      |                  | **~775**   |


### Notable Complexity Factors

- **WebRTC with raw audio frames**: `main.py` (1,244 LOC) implements a full custom WebRTC session using `aiortc`, processing raw `AudioFrame` numpy arrays and piping them live to Deepgram STT via WebSocket — this is low-latency real-time systems work, not standard API integration.
- **Smart Turn v3 ONNX inference**: A Whisper Tiny encoder + classifier head runs locally in <60ms to decide whether silence = turn complete. This is on-device ML inference with prosody analysis — rare, specialist-adjacent territory.
- **4-Layer memory architecture**: `funcs/memory.py` + `funcs/models.py` implement conversation context, episodic summaries, semantic vector search (Mem0), and user profile — all in one coherent async system with SQLite persistence.
- **Dual TTS providers with streaming**: Both ElevenLabs (cloud) and Kokoro (local ONNX, 50ms TTFB) are supported and switchable, with real-time audio chunk streaming.
- **Canvas + GSAP animation pipeline**: The `svg-canvas.tsx` (1,506 LOC) is a full custom drawing engine — shapes, paths, KaTeX math, ink strokes via Rough.js, sequenced GSAP timelines triggered by SSE events. This is a bespoke animation system, not a charting library.
- **Scene Kit (SDL)**: A domain-specific layout language with a compiler and 12 pre-built visual components (coordinate planes, function plots, Venn diagrams, tree diagrams, etc.) that deterministically renders educational visuals from 80-token LLM descriptions.
- **RestrictedPython sandbox**: `tool_executor.py` runs LLM-generated tool code in a sandboxed Python environment with allowlisted imports — security-critical execution pipeline.
- **No production test suite**: 4 test files (351 LOC) cover basic flows but not the real-time pipelines. A production-ready build would require significantly more testing investment (+15-20% effort).

---

## 3. Effort Estimation

### Engineering-Only (Solo Senior Developer)

- **Total estimated hours**: ~775
- At $125/hr average: **~$96,875**

### Team-Adjusted Estimates


| Config       | Multiplier | Total Hours | Blended Rate | Total Cost | Calendar Time |
| ------------ | ---------- | ----------- | ------------ | ---------- | ------------- |
| Solo         | 1.0×       | ~775        | $125/hr      | ~$96,875   | ~5.9 months   |
| Lean Startup | 1.45×      | ~1,124      | $115/hr      | ~$129,260  | ~3.7 months   |
| Growth Co    | 2.2×       | ~1,705      | $115/hr      | ~$196,075  | ~3.9 months   |
| Enterprise   | 2.65×      | ~2,054      | $120/hr      | ~$246,480  | ~4.0 months   |


> Calendar time = Total hours ÷ weekly throughput (Solo: 30 hrs/wk, Lean: 70, Growth: 100, Enterprise: 120) ÷ 4.33 wks/month

---

## 4. Value per Claude Hour

> The following section requires knowing how many hours of Claude Code usage went into building this. The user can fill this in, or use the estimates below as a reference framework.

### Estimation Heuristic

- This codebase is ~12,500 LOC at predominantly Advanced/Complex tier
- At ~400-600 LOC/hour for complex AI/real-time systems work: **estimated ~25–35 Claude active hours**
- Using midpoint estimate: **~30 Claude hours**


| Value Basis                 | Total Value | Claude Hours | $/Claude Hour |
| --------------------------- | ----------- | ------------ | ------------- |
| Engineering only (solo avg) | ~$96,875    | ~30 hrs      | ~$3,229/hr    |
| Full team (Growth Co)       | ~$196,075   | ~30 hrs      | ~$6,536/hr    |


### Speed vs. Human Developer

- Estimated human hours (solo): ~775
- Estimated Claude active hours: ~30
- **Speed multiplier: ~26×**

### Cost Comparison (estimated)

- Human developer cost: ~$96,875 (at $125/hr)
- Estimated Claude cost: ~$600–$900 (Claude Pro at $20/mo × ~2–3 months of active use, or API credits)
- **Net savings: ~$95,000–$96,000**
- **ROI: ~100–160×**

> These Claude cost estimates are highly approximate. Actual API spend depends on context window usage per conversation, tool call volume, and whether Max/Pro subscription or API is used. Adjust accordingly.

---

## 5. Grand Total Summary


| Metric            | Solo        | Lean Startup | Growth Co   | Enterprise  |
| ----------------- | ----------- | ------------ | ----------- | ----------- |
| Calendar Time     | ~5.9 months | ~3.7 months  | ~3.9 months | ~4.0 months |
| Total Human Hours | ~775        | ~1,124       | ~1,705      | ~2,054      |
| Total Cost        | ~$96,875    | ~$129,260    | ~$196,075   | ~$246,480   |


---

## The Headline

This Voice AI codebase represents approximately **$97K–$246K of engineering value** depending on team configuration — the equivalent of a 6-month solo sprint by a senior full-stack developer or a 4-month push by a growth-stage engineering team. What makes this project particularly high-value is the density of specialist work packed into it: real-time WebRTC audio pipelines, on-device ONNX ML inference for turn detection, a 4-layer memory architecture, a bespoke SVG canvas with GSAP animation sequencing, a custom Scene Description Language with a layout compiler, and a sandboxed Python execution engine for LLM-generated tools. At an estimated ~~30 Claude active hours, the effective value delivered per Claude hour is **~~$3,200–$6,500** — a speed multiplier of roughly **26× over solo human development** and an ROI in the range of **100–160×** on AI-assisted development costs.

---

## Assumptions

1. Rates based on US market averages (2025–2026), senior talent, W2-equivalent contractor rates
2. Solo baseline assumes a senior full-stack developer with 5+ years experience and familiarity with real-time audio/AI systems
3. **No production test suite**: only 4 test files covering basic flows; a production-hardened build would add ~15–20% to costs
4. Does not include: marketing, legal, hosting/infrastructure (Deepgram, ElevenLabs, OpenAI API costs), or ongoing maintenance
5. Claude cost estimate is a rough approximation; actual API spend varies significantly by usage pattern
6. The codebase is greenfield/new development, not a refactor of existing code
7. LOC counts exclude generated files (`package-lock.json`, `tsconfig.tsbuildinfo`, audio/model binaries, `.pycache`)


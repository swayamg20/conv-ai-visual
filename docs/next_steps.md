Till Now:
- chat
- basic STT - VAD - LLM - TTS
- tool call - ()
- memory
- db
- canvas API

Feat/TO DO:
- redis
- web search live
- integration with calender, events
- re-routing LLM calls (based on complexities) for better latency
- try out different LLMs, SLMs, classifiers
- vector search scope discovery
- sandbox coding the tools and execute them live
- https://jsoncanvas.org/ for canvas API
- less priority:
  - quartz based documentation

In Test:
- Interuption Handling

# Decision Intelligence Model – Training Data Collection Plan

## Purpose of This Document

This document defines a **concrete, execution-ready plan** to collect high-signal training data for building a **Decision Intelligence Core** (fine-tuned SLM) that converts vague human intent into structured decision state.

This is **not** a generic ML data plan. It is designed to:

* Minimize cost and annotation overhead
* Capture *decision psychology*, not surface preferences
* Compound into a defensible data moat  

---

## 1. What We Are Training (Ground Truth)

### Model Type

* Fine-tuned **SLM (3B–7B)**
* Task: **Decision State Extraction**

### Canonical Output Schema (v1)

Every user utterance is mapped to a structured decision state:

* decision_stage: exploring | narrowing | validating | committing
* confidence_score: 0.0 – 1.0
* risk_tolerance: low | medium | high
* budget_anxiety: low | medium | high
* energy_preference: low | medium | high
* novelty_preference: low | medium | high
* time_flexibility: low | medium | high
* dominant_tradeoff: (e.g. comfort_vs_cost, pace_vs_coverage)
* hesitation_markers: boolean / categorical

This schema is **domain-agnostic** and reusable beyond travel.

---

## 2. Core Data Principle

> We do not ask users to label themselves.
> We infer labels from **choices, reactions, and hesitation**.

High-quality decision data is *behavioral*, not declarative.

---

## 3. Primary Data Collection Channels

### 3.1 Conversational Shadow Labeling (Primary Source)

#### Description

Design conversational flows where **every question probes a latent variable**.

Example probe:

> "If this plan costs ~₹10k more but feels calmer, does that bother you?"

#### Signals Extracted

* Budget anxiety
* Comfort bias
* Risk tolerance

#### Labeling Method

* Deterministic rules + LLM-assisted extraction
* No human annotation required initially

#### Why This Is Critical

* Zero user friction
* Continuous data generation
* Labels grounded in *real decision moments*

---

### 3.2 Pairwise Trade-off Choices

#### Description

Users choose between **two imperfect options**.

Example:

* A) Short, relaxed trip
* B) Longer, more packed trip

#### Signals Extracted

* Energy preference
* Pace tolerance
* Novelty appetite

#### Storage

* Preference vector update per choice

#### Advantage

Humans reveal preferences far more accurately through trade-offs than explanations.

---

### 3.3 Regret & Relief Collection (Post-Decision)

#### Trigger Points

* After finalizing a plan
* After trip completion

#### Questions (Only 1–2)

* "What worked better than expected?"
* "What would you change next time?"

#### Signals Extracted

* Regret indicators
* Confidence calibration
* Preference correction

This data is extremely rare and highly valuable.

---

## 4. Bootstrapping Data (Cold Start)

### 4.1 Past Decision Reconstruction Interviews

#### Method

* 30–60 min interviews
* Ask users to describe:

  * A trip they loved
  * A trip they disliked

#### Volume Target

* 100–300 stories

#### Value

* High-density preference signals
* No live product required

---

### 4.2 Planner / Agent Chat Logs (If Available)

#### Sources

* Boutique travel agents
* Concierge services

#### Requirements

* Anonymized
* Consent-based

#### Why This Data Is Gold

* Real money at stake
* Authentic hesitation language
* High emotional intensity

---

### 4.3 Synthetic Conversations (Temporary)

#### Usage

* Only for schema validation and pipeline testing
* Generated via strong LLMs

#### Hard Rule

Synthetic data must **never dominate** training distribution.

---

## 5. Labeling Strategy

### 5.1 Auto-Labeling

* Rule-based extraction from probe responses
* LLM-assisted structuring (with strict output schema)

### 5.2 Human Audit

* 5–10% sampled weekly
* Focus on:

  * Low confidence predictions
  * High ambiguity cases

### 5.3 Active Learning Loop

* SLM flags uncertain extractions
* These are prioritized for review and improved probes

---

## 6. Confidence Tracking (Feedback Loop)

### Metric

Ask users:

> "How confident do you feel about this decision? (0–10)"

### Timing

* Before planning
* After planning
* During execution (optional)
* Post completion

### Usage

* Train confidence prediction
* Measure reassurance effectiveness

---

## 7. Training Pipeline

1. Collect conversational data
2. Extract implicit signals
3. Auto-label into decision schema
4. Sample for human audit
5. Fine-tune SLM
6. Deploy updated model
7. Measure confidence delta
8. Iterate weekly

---

## 8. What We Explicitly Do NOT Collect

* Scraped itineraries
* Blog content
* Static surveys
* One-time preference forms
* Generic reviews

These do not encode decision dynamics.

---

## 9. Milestones

### Phase 1 (Weeks 1–4)

* Finalize schema v1
* Collect 300–500 decision conversations
* First SLM fine-tune

### Phase 2 (Weeks 5–8)

* Add regret/relief loop
* Introduce active learning
* Improve probe questions

### Phase 3 (Weeks 9–12)

* Stabilize confidence prediction
* Reduce reliance on LLM extraction
* Prepare for domain extension

---

## 10. North-Star Metric

> **Reduction in user uncertainty per interaction**

Measured via:

* Confidence delta
* Decision time reduction
* Repeat usage

---

## Final Note

This data strategy compounds.
Each conversation makes the system:

* more accurate
* more personal
* harder to replicate

This is the true moat — not the model size, not the UI.

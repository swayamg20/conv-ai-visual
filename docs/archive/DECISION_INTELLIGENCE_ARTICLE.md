# Beyond Preferences: Building a Decision Intelligence Model for AI Agents

> How to capture what users really want by studying their hesitations, not their declarations

## The Problem with Preference Data

Every AI assistant asks the same questions:
- "What do you prefer?"
- "Tell me your budget"
- "How would you describe your ideal trip?"

Users answer confidently. The AI takes notes. **And it's all garbage.**

Not because users lie, but because **humans are terrible at articulating what they want**. They'll say "budget-friendly" then book the expensive hotel. They'll request "adventure" then pick the safest option. They'll claim "flexibility" then panic at any deviation.

Traditional preference learning treats these declarations as ground truth. It shouldn't.

## What is Decision Intelligence?

Decision Intelligence isn't about capturing what users *say* they want. It's about modeling their **decision psychology** — the latent variables that drive actual choices.

Instead of asking "What's your budget?", we observe:
- Do they hesitate when a price increases?
- Do they justify upgrades vs just accept them?
- Do they compare prices or ignore them?

This isn't preference learning. It's **decision state extraction**.

## The Core Insight: Choices Over Claims

Here's the shift:

**Traditional approach:**
```
User: "I want a relaxing, budget-friendly trip"
System: [Optimizes for low-cost + low-activity]
Reality: User books expensive spa resort
```

**Decision Intelligence approach:**
```
System: "This costs ₹10k more but feels calmer. Bother you?"
User: "Hmm... I mean, maybe if it's really worth it?"
System: [Extracts: budget_anxiety: medium, comfort_preference: high, confidence_score: 0.6]
```

The second approach captures **decision dynamics**, not static preferences.

## The Architecture: Decision State Schema

Every user utterance maps to a structured decision state:

```python
{
  "decision_stage": "exploring | narrowing | validating | committing",
  "confidence_score": 0.0 - 1.0,
  "risk_tolerance": "low | medium | high",
  "budget_anxiety": "low | medium | high",
  "energy_preference": "low | medium | high",
  "novelty_preference": "low | medium | high",
  "time_flexibility": "low | medium | high",
  "dominant_tradeoff": "comfort_vs_cost | pace_vs_coverage | ...",
  "hesitation_markers": ["but_qualifier", "maybe_hedge", "justify_reasoning"]
}
```

**Key principle:** This schema is **domain-agnostic**. Whether you're building for travel, shopping, career advice, or healthcare, humans make decisions the same way.

## Data Collection: Three High-Signal Channels

### 1. Conversational Shadow Labeling (Primary Source)

Design every question to probe a latent variable.

**Bad question:**
> "What's your budget?"

**Good question (probes budget_anxiety):**
> "If this plan costs ~₹10k more but feels calmer, does that bother you?"

**Extraction logic:**
```python
if user_response contains ["bother", "concerned", "too much"]:
    budget_anxiety = "high"
elif user_response contains ["maybe", "depends", "worth it"]:
    budget_anxiety = "medium"
    confidence_score = 0.6  # Hesitation detected
elif user_response contains ["fine", "okay", "no problem"]:
    budget_anxiety = "low"
```

**Why this works:**
- Zero user friction (they're just chatting)
- Labels grounded in real decision moments
- Continuous data generation
- No expensive human annotation

**Real conversation example:**

```
AI: "Two options: A) 3 days, super relaxed. B) 5 days, packed schedule. Which pulls you?"

User: "I mean... I like doing things, but I also don't want to be exhausted..."

Extracted state:
- energy_preference: medium
- decision_stage: validating
- confidence_score: 0.45
- hesitation_markers: ["but_qualifier", "also_qualifier"]
- dominant_tradeoff: energy_vs_coverage
```

### 2. Pairwise Trade-off Choices

Humans reveal preferences far more accurately through **imperfect choices** than explanations.

**The method:**
Present two options where neither is strictly better:

```
A) Short trip (3 days) | Relaxed pace | Higher per-day cost
B) Long trip (5 days) | Packed schedule | Lower per-day cost
```

**What you learn:**
- Choice A → Prefers: comfort, energy management, willing to pay for quality time
- Choice B → Prefers: coverage, novelty, cost optimization, high energy tolerance

**Implementation:**
```python
# Store as preference vector, not discrete choice
user_choices = {
    "energy_management_vs_coverage": 0.7,  # Chose A (relaxed)
    "time_efficiency": 0.3,                 # Chose A (shorter)
    "cost_sensitivity": 0.6                 # Chose A (higher cost)
}
```

**Why pairwise is superior:**
- Forces explicit trade-offs
- No "I want everything" problem
- Reveals relative priorities
- Easy to generate (no annotation)

### 3. Regret & Relief Collection (Post-Decision)

This is the **highest-signal, rarest data**.

**Timing:**
- After finalizing a plan (pre-execution)
- After trip completion (post-execution)

**Questions (only 1-2):**
> "What worked better than expected?"
> "What would you change next time?"

**Example response:**
```
User: "Honestly, the downtime was boring. Wish we'd packed more in, 
       even if it meant being tired."

Extracted correction:
- energy_preference: INCREASE from 0.4 to 0.7
- boredom_risk_tolerance: DECREASE
- confidence_calibration: User underestimates their energy capacity
```

**Why this is gold:**
- Real money was at stake
- Authentic emotional intensity
- Reveals prediction errors in user's self-model
- Enables confidence calibration

## Bootstrapping: Cold Start Strategies

You can't wait for live users. Here's how to get initial data:

### Past Decision Reconstruction (100-300 interviews)

**Method:**
60-minute interviews asking users to describe:
1. A decision they loved
2. A decision they regretted

**Questions to ask:**
- "Walk me through how you decided"
- "What almost made you choose differently?"
- "What surprised you about the outcome?"
- "If you could redo it, what changes?"

**Value:**
- High-density preference signals
- Natural language (trains better models)
- No live product required

### Real Agent Chat Logs (If Available)

**Sources:**
- Boutique travel agents
- Concierge services
- Customer support transcripts

**Why this data is superior:**
- Real money at stake
- Authentic hesitation language
- High emotional intensity
- Shows actual negotiation dynamics

**Requirements:**
- Anonymization
- User consent
- Data licensing agreements

### Synthetic Data (Temporary Only)

**Usage:** Schema validation and pipeline testing only

**Generation approach:**
```python
prompt = """
Generate a conversation where a user is deciding on a trip.
Include:
- Budget hesitations
- Energy level concerns
- Trade-off discussions
- Confidence uncertainty
Output in JSON matching our decision state schema.
"""
```

**Hard rule:** Synthetic data must **never dominate** training distribution. Use it to bootstrap, then replace with real data ASAP.

## The Labeling Strategy

### Auto-Labeling (Primary)

```python
class DecisionStateExtractor:
    def extract_budget_anxiety(self, response: str) -> str:
        # Rule-based extraction
        high_anxiety_patterns = ["expensive", "too much", "can't afford", "concerned"]
        medium_patterns = ["maybe", "depends", "if it's worth", "hmm"]
        low_patterns = ["fine", "no problem", "okay with"]
        
        # Check patterns with context
        if any(pattern in response.lower() for pattern in high_anxiety_patterns):
            return "high"
        # ... similar for medium/low
    
    def extract_hesitation_markers(self, response: str) -> List[str]:
        markers = []
        if "but" in response: markers.append("but_qualifier")
        if any(word in response for word in ["maybe", "possibly"]): 
            markers.append("uncertainty_hedge")
        if len(response) > 100 and "because" in response:
            markers.append("over_justification")
        return markers
```

**Advantages:**
- Zero annotation cost
- Instant labeling
- Consistent criteria
- Scales infinitely

### Human Audit (5-10% sample)

**Focus areas:**
- Low confidence predictions
- High ambiguity cases
- Edge cases (sarcasm, cultural differences)

**Workflow:**
```python
if model_confidence < 0.7:
    flag_for_human_review(conversation_id)

# Weekly review session
for flagged_conversation in review_queue:
    expert_label = human_annotator.review(conversation)
    if expert_label != model_label:
        add_to_training_corrections(conversation, expert_label)
```

### Active Learning Loop

```python
class ActiveLearner:
    def should_review(self, prediction):
        # Flag uncertain extractions
        if prediction.confidence < 0.6:
            return True
        
        # Flag predictions near decision boundaries
        if 0.45 < prediction.budget_anxiety < 0.55:
            return True
        
        # Flag rare patterns
        if prediction.dominant_tradeoff not in common_tradeoffs:
            return True
        
        return False
```

## Training Pipeline (Weekly Iteration)

```
1. Collect conversational data
   ↓
2. Auto-label with extraction rules + LLM
   ↓
3. Sample uncertain cases for human audit
   ↓
4. Fine-tune SLM (3B-7B parameters)
   ↓
5. Deploy updated model
   ↓
6. Measure confidence delta (key metric)
   ↓
7. Identify failure modes
   ↓
8. Improve probe questions
   ↓
(Loop back to step 1)
```

### Model Training

**Model choice:** Fine-tuned SLM (3B-7B parameters)
- Fast inference (<50ms)
- Runs on-device or edge
- Privacy-preserving
- Cost-effective at scale

**Training objective:**
```python
# Multi-task learning
loss = (
    classification_loss(decision_stage) +
    regression_loss(confidence_score) +
    classification_loss(risk_tolerance) +
    # ... other latent variables
    reconstruction_loss(conversation_embedding)
)
```

**Training data format:**
```json
{
  "conversation": [
    {"role": "assistant", "content": "If this costs ₹10k more but feels calmer, does that bother you?"},
    {"role": "user", "content": "I mean... maybe if it's really worth it?"}
  ],
  "labels": {
    "budget_anxiety": "medium",
    "confidence_score": 0.6,
    "hesitation_markers": ["uncertainty_hedge"],
    "dominant_tradeoff": "comfort_vs_cost"
  }
}
```

## The North Star Metric

Traditional AI: Accuracy, F1 score, perplexity

Decision Intelligence: **Reduction in user uncertainty per interaction**

**Measurement:**
```python
confidence_delta = (
    confidence_score_after_conversation - 
    confidence_score_before_conversation
)

# Target: +0.15 to +0.25 per meaningful interaction
```

**Why this matters:**
- Good decisions feel certain
- Users return when they trust the guidance
- Reduces decision paralysis
- Creates defensible moat

**Secondary metrics:**
- Decision time reduction
- Repeat usage rate
- Post-decision satisfaction
- Regret rate (lower is better)

## Real-World Example: Travel Planning

**Before (Traditional Preferences):**
```
System: "What's your budget?"
User: "Around ₹50,000"
System: [Shows options ≤₹50k, user books nothing]
```

**After (Decision Intelligence):**
```
System: "I'm seeing two paths. A) ₹45k, solid basics. B) ₹58k, notably more comfortable. 
         Where does your gut pull?"
User: "Ugh, I mean... ₹58k feels like a lot, but if the difference is big..."
System: [Extracts: budget_anchor=₹50k, flex_tolerance=₹8k, comfort_weighted, 
         confidence=0.55, needs_reassurance]
System: "The ₹58k option gets you a pool, better location, and private transport. 
         Most people at this anchor feel it's worth it. Still feeling uncertain?"
User: "Actually, yeah, let's do that."
System: [Logs: confidence_boost=+0.25, decision_made=true, reassurance_effective=true]
```

**Key difference:** The system understood the user's **decision dynamics**, not just their stated budget.

## What You Explicitly Don't Collect

Avoid these data sources — they don't encode decision dynamics:

- ❌ Scraped itineraries (no context on choices)
- ❌ Blog content (post-hoc rationalization)
- ❌ Static surveys (declared preferences)
- ❌ One-time preference forms (no uncertainty signals)
- ❌ Generic reviews (outcome bias)

**Why they fail:** They capture outcomes, not the decision process.

## Implementation Milestones

### Phase 1 (Weeks 1-4): Foundation
- [ ] Finalize decision state schema v1
- [ ] Design 20-30 probe questions
- [ ] Build auto-labeling pipeline
- [ ] Collect 300-500 decision conversations
- [ ] First SLM fine-tune
- [ ] Measure baseline confidence delta

### Phase 2 (Weeks 5-8): Feedback Loops
- [ ] Add regret/relief collection
- [ ] Implement active learning
- [ ] Improve probe questions based on data
- [ ] Human audit workflow
- [ ] Reach 2,000+ labeled conversations

### Phase 3 (Weeks 9-12): Scale & Optimize
- [ ] Stabilize confidence prediction
- [ ] Reduce reliance on LLM extraction (faster inference)
- [ ] Deploy edge model (on-device)
- [ ] Prepare for domain extension (beyond travel)
- [ ] Hit target confidence delta (+0.20 per interaction)

## Why This Creates a Moat

Traditional AI agents compete on:
- Model size (anyone can use GPT-4)
- UI polish (copyable)
- Integration breadth (commoditized)

Decision Intelligence compounds:
- **More conversations → Better understanding**
- **Better understanding → Higher confidence deltas**
- **Higher confidence deltas → More repeat users**
- **More repeat users → More data**

Each conversation makes the system:
- More accurate in state extraction
- More personalized per user
- Harder to replicate (data moat)

**This is the true moat** — not the model size, not the UI, not the API integrations.

## Technical Stack (Reference Implementation)

For our voice AI project, here's the integration:

```python
# funcs/decision_intelligence.py

class DecisionStateExtractor:
    def __init__(self, model_path: str):
        self.model = load_finetuned_slm(model_path)
    
    async def extract_state(
        self, 
        conversation_history: List[Dict],
        current_utterance: str
    ) -> DecisionState:
        """Extract decision state from conversation."""
        
        # Auto-labeling with rules + model
        rules_output = self._apply_rules(current_utterance)
        model_output = await self._model_inference(
            conversation_history, 
            current_utterance
        )
        
        # Ensemble (rules + model)
        return self._merge_outputs(rules_output, model_output)
    
    def _apply_rules(self, utterance: str) -> dict:
        """Fast rule-based extraction."""
        return {
            "budget_anxiety": self._extract_budget_anxiety(utterance),
            "hesitation_markers": self._extract_hesitation(utterance),
            # ... other rule-based extractions
        }
    
    async def _model_inference(self, history, utterance):
        """SLM inference for complex patterns."""
        prompt = self._format_prompt(history, utterance)
        output = await self.model.generate(prompt)
        return self._parse_model_output(output)

# Integration in main.py
from funcs.decision_intelligence import DecisionStateExtractor

decision_extractor = DecisionStateExtractor("models/decision_slm_v1")

# After each user utterance
decision_state = await decision_extractor.extract_state(
    conversation_history=conversation_contexts[pc_id],
    current_utterance=transcript
)

# Use decision state to guide responses
if decision_state.confidence_score < 0.6:
    # Provide reassurance
    system_prompt = build_reassurance_prompt(decision_state)
elif decision_state.decision_stage == "committing":
    # Ask for confirmation
    system_prompt = build_confirmation_prompt(decision_state)
```

## Beyond Travel: Domain Extension

The decision state schema is **domain-agnostic**. Here's how it applies elsewhere:

**E-commerce (Product Selection):**
- budget_anxiety → price_sensitivity
- energy_preference → convenience_preference
- novelty_preference → brand_loyalty

**Career Advice:**
- risk_tolerance → career_risk_tolerance
- time_flexibility → timeline_urgency
- dominant_tradeoff → salary_vs_impact, growth_vs_stability

**Healthcare (Treatment Decisions):**
- risk_tolerance → intervention_acceptance
- confidence_score → decision_readiness
- hesitation_markers → information_needs

**Financial Planning:**
- budget_anxiety → loss_aversion
- novelty_preference → investment_adventurousness
- time_flexibility → time_horizon

## Conclusion: The Shift from Preferences to Psychology

The future of AI agents isn't better language models or faster APIs. It's understanding **how humans actually make decisions**.

Stop asking what users want. Start observing how they decide.

- Capture hesitations, not declarations
- Model uncertainty, not just preferences
- Learn from regrets, not just successes
- Extract latent variables, not surface features

This is Decision Intelligence. And it compounds into the only moat that matters.

---

## Resources & Further Reading

**Papers:**
- "Human Decision-Making under Uncertainty" (Kahneman & Tversky)
- "Preference Learning" (Fürnkranz & Hüllermeier)
- "Active Learning for Preference Elicitation" (Boutilier et al.)

**Implementation:**
- Fine-tuning SLMs: Hugging Face Transformers
- Conversation management: LangChain, LlamaIndex
- Schema validation: Pydantic
- Data collection: PostHog, Segment (with custom events)

**Related work:**
- Chatbot user modeling
- Recommender systems with implicit feedback
- Behavioral economics in AI

---

*This article is based on production learnings from building voiceai - a real-time voice AI assistant with decision intelligence capabilities.*

**Author:** [Your Name]
**Code:** https://github.com/[your-repo]
**Date:** January 2026


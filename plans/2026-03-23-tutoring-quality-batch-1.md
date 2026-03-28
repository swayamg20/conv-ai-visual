# Improve Tutoring Quality for Agent-Backed Physics Sessions

## Purpose / Big Picture

This ExecPlan covers the next tutoring-quality batch for `feat/gstack-review`. The goal is not to add more platform breadth. The goal is to make an existing education session feel meaningfully better to a real student using an agent-backed tutoring flow.

After this work, a student using a physics tutor agent should experience three visible improvements. First, knowledge from prior sessions should actually influence the next session instead of existing only in the database. Second, the tutor should produce more appropriate visual explanations for common physics problems instead of generic whiteboard output. Third, uploaded class materials should be used more reliably even when the student’s phrasing does not match the exact wording in the source.

Success is observable in one concrete scenario: create or reuse an agent with canvas and resources, run one session that ends with stored tutoring memory, start a second session with the same agent, ask a follow-up question on a related topic, and observe that prior-session context, visual pedagogy, and resource lookup all improve the answer.

## Progress

- [x] 2026-03-23 11:03 IST: Created the ExecPlan and grounded it in the current repository state.
- [x] 2026-03-23 11:20 IST: Audited the current agent-backed tutoring flow in code and captured the exact runtime validation path and logs to watch.
- [x] 2026-03-23 11:24 IST: Tightened the physics-specific tutor prompting in `funcs/agents.py`.
- [x] 2026-03-23 11:28 IST: Replaced plain `LIKE` retrieval ranking with SQL candidate filtering plus lexical reranking in `funcs/resources.py` and `funcs/models.py`.
- [x] 2026-03-23 11:34 IST: Completed backend syntax and frontend typecheck verification for the code changes.
- [ ] Run one operator-visible tutoring scenario in a real authenticated environment.
- [ ] Update this document with discoveries, decisions, and final outcomes as the work lands.

## Surprises & Discoveries

As of plan creation, several pieces of the quality path already exist but are only partially validated. Session cleanup, abrupt-disconnect summaries, token-budget prompt assembly, and `agent_id` propagation have already been improved locally, which means this batch is less about inventing new plumbing and more about proving the tutoring loop works end to end.

The current retrieval path is still materially weaker than the tutoring vision. `funcs/resources.py` delegates search to `ResourceChunkRepo.search()`, and that repository method in `funcs/models.py` currently performs a simple multi-word `LIKE` match over chunk text. There is no embedding or vector field in `ResourceChunkModel` yet, so any retrieval improvement here will either need a lightweight schema change or a ranking strategy that works with the existing schema.

The current agent prompt compiler in `funcs/agents.py` is generic. It has good structure for persona, learning style, and tool access, but it does not yet contain domain-specific instruction for physics diagrams such as free-body diagrams, inclined planes, projectile motion, or graph interpretation. That means even a correct canvas-capable agent may still underperform as a tutor.

The retrieval upgrade did not require a schema change. The chosen implementation keeps SQL as the candidate filter but reranks the matches in Python using normalized term coverage, token-frequency density, exact-phrase boost, and a small early-match boost. This is intentionally smaller than a full embedding system and fits the current branch better.

The code audit confirmed the local runtime scenario that still matters most: create `Session A` with a real `agent_id`, end it explicitly, then create `Session B` with the same agent and ask what the student struggled with previously. The useful logs are already present in `main.py` and `funcs/memory.py`, so the remaining uncertainty is runtime behavior, not missing observability.

The verification environment is split. `python3` on this machine is Python 3.13 and passes `py_compile`, but the checked-in `venv` is Python 3.9.6. A bounded `from main import app` smoke test in that virtualenv fails on modern `str | None` type syntax in `funcs/config.py`, so the venv is not currently a valid runtime target for the branch without a Python upgrade.

## Decision Log

2026-03-23, Codex: This batch will be implemented as one cohesive ExecPlan instead of three small plans. The student-visible outcome depends on memory reuse, pedagogy, and retrieval working together, and splitting them into isolated plans would hide the real acceptance path.

2026-03-23, Codex: The plan will prioritize end-to-end validation before broader AI polish. The branch already contains memory and session hardening work, so the next risk is believing that the tutoring loop is improved when the user-visible experience has not actually changed.

2026-03-23, Codex: Retrieval improvement should stay scoped to this branch. A full production-grade retrieval stack is not the goal here. The change should be the smallest approach that noticeably improves relevance for student questions over uploaded materials.

2026-03-23, Codex: The retrieval upgrade will stay lexical for now rather than introducing a new embedding dependency or client-side RAG path. The current backend tutoring architecture already has a server-side `search_resources` tool, so a small relevance upgrade is the fastest way to improve student-facing results.

## Outcomes & Retrospective

This section is still in progress. So far, the batch has landed two code improvements: the tutor prompt now contains explicit physics-diagram guidance, and resource retrieval now uses lexical reranking instead of raw keyword matching. Static verification is complete. Final completion still depends on end-to-end validation of an agent-backed tutoring scenario in a real authenticated environment, and the checked-in Python 3.9 virtualenv is currently too old for the branch runtime.

## Context and Orientation

In this repository, an "agent" is the saved tutoring persona and capability configuration stored in the backend and loaded into chat sessions through `AgentRepo` in `main.py`. A "chat session" is the SSE-backed text tutoring session handled by `/chat` in `main.py`. A "voice session" is the WebRTC tutoring flow created by `/offer` in `main.py`. "Cross-session memory" means session summaries and persisted conversation messages being reused when a student comes back to the same agent later. "Canvas mode" means the tutor has access to the whiteboard-style visual tools and is expected to explain by drawing rather than only by speaking.

The important backend entry point is `main.py`. It resolves the authenticated user, resolves or creates a session, loads the selected agent, creates `LLMPipeline`, and attaches callbacks and tools. This is where agent-backed sessions, resumed sessions, and resource-aware tool registration all meet.

`funcs/llm_pipeline.py` is the runtime orchestration layer for the LLM. It owns the active system prompt, the decision to build enriched context, and the interaction with `MemoryManager`. If memory is present but not influencing the actual prompt in a meaningful way, the root cause will usually be visible here or in its call sites.

`funcs/memory.py` owns the short-term conversation window, the user profile layer, episodic summaries, semantic memory integration, and cross-session summary injection. This file already contains prompt budgeting and logging for selected memory sections. The key question for this batch is whether those memory layers are actually shaping the tutoring prompt in a way that survives resumed agent sessions and budget pressure.

`funcs/agents.py` compiles a tutoring persona plus capability list into the system prompt that the pipeline receives. This is where domain-specific teaching behavior belongs for the current branch. If the tutor should strongly prefer free-body diagrams, stepwise derivations, or graph interpretation patterns for physics, those instructions need to live here in clear language.

`funcs/resources.py` and the resource chunk repository in `funcs/models.py` own ingestion and retrieval for uploaded PDFs and URLs. Today, the ingestion path chunks source text and stores it in SQLite, but search is still keyword-based. If a student asks for "momentum is conserved in collisions" and the PDF says "linear momentum remains constant in an isolated system," the current path may miss the relevant chunk. That is the retrieval gap this batch should reduce.

No frontend route changes are required for the first version of this batch unless the validation work shows that the client is failing to send or preserve the agent/session state correctly. If that happens, the plan should be amended before implementation proceeds.

## Plan of Work

Start by validating the current tutoring loop before changing behavior. Run an agent-backed session through creation, message persistence, summary creation, and resumption. Use the existing logging in `main.py` and `funcs/memory.py` to confirm whether the resumed session binds to the canonical `agent_id`, whether prior summaries are loaded, and whether budget trimming drops important context. This first milestone should end with a short factual note in this plan describing exactly what worked and what still failed.

Once the current behavior is understood, tighten the pedagogy layer in `funcs/agents.py`. The change should be opinionated and specific to physics tutoring. Add guidance that tells the agent when to use free-body-style breakdowns, when to draw axes and trajectories, when to plot relationships, and how to narrate step-by-step problem solving while using the canvas. This should improve the quality of existing visual capability without requiring a new tool.

After the prompt layer is stronger, improve retrieval enough that uploaded study materials matter in more student conversations. The preferred path is a lightweight relevance upgrade that fits this branch. If embeddings are introduced, the plan must include the necessary schema, ingestion updates, and a fallback for environments without the embedding dependency or API key. If embeddings are not introduced, the alternative must still outperform raw `LIKE` matching in a measurable way, for example by normalizing terms, scoring overlap, or reranking candidate chunks. The plan should favor the smallest approach that can be verified with a concrete question against a resource.

Finish by validating the tutoring loop again with the same scenario used at the beginning. The final result should prove that the student gets better recall from prior sessions, better visual explanations, and better use of uploaded material, not just that the code compiles.

## Concrete Steps

1. Establish the baseline from repository root.

    Run:
        python3 -m py_compile main.py funcs/*.py
        cd web && npx tsc --noEmit

    Expected result: the current branch state is syntactically clean before new tutoring-quality edits begin.

2. Verify the current agent-backed session lifecycle from repository root.

    Use the existing API flow with an authenticated Firebase token. Create or select an agent, send a `/chat` request with `agent_id`, continue the session, end it, then start a second session for the same agent and inspect logs.

    Expected result: logs show session binding, persisted messages or summary generation, and whether cross-session context was loaded or skipped by budget.

3. Strengthen the tutoring prompt in `funcs/agents.py`.

    Edit the compiled agent prompt so a physics tutor explicitly prefers the right visual explanation pattern for common mechanics and graph-based problems.

    Expected result: a canvas-capable physics agent produces more specific visual instructions without requiring changes in client behavior.

4. Improve retrieval in `funcs/resources.py` and, if necessary, `funcs/models.py`.

    First inspect whether the existing schema is sufficient for a lightweight ranking improvement. If not, add only the smallest schema or repository changes required for the chosen approach.

    Expected result: resource search returns more relevant chunks for semantically similar student questions than the current plain keyword path.

5. Re-run verification from repository root.

    Run:
        python3 -m py_compile main.py funcs/*.py
        python3 -c "from main import app"
        cd web && npx tsc --noEmit

    Expected result: backend imports cleanly, frontend types still pass, and there are no accidental regressions from the quality patch.

6. Run a final tutoring scenario.

    The operator should exercise one agent-backed physics flow that uses prior-session context and one resource-backed question against uploaded material.

    Expected result: evidence exists in logs or response content that the quality patch changed actual tutoring behavior, not just internal plumbing.

## Validation and Acceptance

Acceptance for this ExecPlan is not "tests passed." Acceptance requires a convincing tutoring scenario.

The memory acceptance path is: run one agent-backed session, end it so a summary or persisted context exists, start a second session with the same agent, and confirm from logs and response behavior that prior-session knowledge was available to the tutor. The plan fails if the database contains memory but the resumed tutoring prompt does not visibly benefit from it.

The physics-prompt acceptance path is: ask the tutor at least one inclined-plane or free-body-style question and one graph-oriented or projectile-style question. The answer should clearly prefer an appropriate visual structure instead of generic narration. If the tutor still behaves like a generic diagram bot, this part of the plan is not done.

The retrieval acceptance path is: upload or reuse a resource whose wording is not an exact lexical match for the student’s question, then ask a semantically related question through the tutoring flow. The answer should either call `search_resources` with a useful query and pull back the right chunk, or otherwise demonstrate that the new retrieval path materially improved relevance. If only exact keyword overlap works, this part of the plan is not done.

The minimum verification commands for a completed implementation are:

    python3 -m py_compile main.py funcs/*.py
    python3 -c "from main import app"
    cd web && npx tsc --noEmit

If optional dependencies such as `mem0` or an embedding provider are unavailable in the local shell, record that limitation in `Surprises & Discoveries` and provide the strongest fallback evidence that can still be produced.

# Drive Murmur to Verified Tutoring Readiness with a Living Multi-Agent Loop

## Purpose / Big Picture

This ExecPlan defines how work on Murmur should proceed from this point forward on branch `codex-multi-agent-execplan-2026-03-27`. The goal is not to ship isolated fixes and then stop. The goal is to keep moving through the next real bottleneck until Murmur is verified as a strong product for its current wedge: a voice-first AI tutor that explains physics and math by drawing.

For this branch, success is product-visible and operator-visible. A student should be able to sign in, start a tutoring session, talk naturally, see useful visual explanations, resume the same tutor later, and benefit from prior-session memory and uploaded learning material. An operator should be able to prove that the system is behaving correctly through logs, traces, validation commands, and at least one concrete end-to-end tutoring scenario.

This plan is intentionally a living control document for long-running work. It must be updated throughout execution, not only at the beginning or end. The execution loop for this branch is: define the next exact batch, run bounded parallel subagents where they reduce noise, complete and verify the batch, inspect what still blocks product quality, then either extend this plan or create the next dated ExecPlan before continuing. Do not treat this as a one-time planning artifact.

## Progress

- [x] 2026-03-27 22:20 IST: Re-read `.agent/PLANS.md` and confirmed the repository requires living ExecPlans for multi-hour, cross-cutting work.
- [x] 2026-03-27 22:23 IST: Audited existing planning and product docs in `docs/` and `plans/` to ground the next batch in the repo's current direction.
- [x] 2026-03-27 22:26 IST: Created and switched to branch `codex-multi-agent-execplan-2026-03-27` so this session stays isolated.
- [x] 2026-03-27 22:34 IST: Reviewed official Codex and OpenAI documentation on long-running work, multi-agent delegation, prompt structure, and session controls.
- [x] 2026-03-27 22:41 IST: Created this ExecPlan as the controlling document for the next execution loop.
- [x] 2026-03-27 02:46 IST: Re-established the baseline. `python3 -m py_compile main.py funcs/*.py` passed and `cd web && npx tsc --noEmit` passed. The system-Python import smoke check failed because `numpy` is missing in that shell.
- [x] 2026-03-27 02:58 IST: Traced the session continuity path across `main.py`, `funcs/memory.py`, `funcs/llm_pipeline.py`, and `funcs/models.py`. Confirmed chat continuity was mostly wired, while voice continuity had real structural gaps.
- [x] 2026-03-27 03:11 IST: Implemented the first code batch in `main.py`, `web/src/hooks/use-webrtc.ts`, and `web/src/app/(app)/session/[agentId]/page.tsx` to make agent-backed voice sessions participate in the persistent session and cross-session memory model.
- [x] 2026-03-27 03:14 IST: Re-ran `python3 -m py_compile main.py funcs/*.py` and `cd web && npx tsc --noEmit` after the patch. Both passed.
- [x] 2026-03-27 12:08 IST: Hardened the Python import path. `funcs/__init__.py` now lazy-loads exports instead of eagerly importing optional heavy modules, and Smart Turn initialization moved off module import and onto first voice use.
- [x] 2026-03-27 12:08 IST: Re-ran the stronger backend runtime smoke check in a provisioned Python 3.12 environment. `/tmp/voiceai-py312/bin/python -c "from main import app"` now completes and prints `FastAPI`.
- [x] 2026-03-27 12:08 IST: Fixed the browser chat continuity path so `useChat()` sends Firebase auth headers and `agent_id` to `/chat`, keeping text tutoring aligned with the protected persistent-session model.
- [x] 2026-03-27 12:08 IST: Added explicit end-of-session summary UX on the agent session page. An intentional voice disconnect now waits for `/api/sessions/{id}/end` and shows the returned recap and mastery count to the student.
- [x] 2026-03-27 12:17 IST: Added and ran `test/test_authenticated_session_continuity.py` in the Python 3.12 environment. The test monkeypatches the authenticated boundary and LLM calls, then proves the local continuity loop end to end: create agent, create session, chat twice, end session, create a new session with the same agent, and verify that the resumed response references the prior-session summary.
- [ ] Run the first execution batch end to end in an authenticated environment: prove that voice or chat tutoring with a real agent produces a durable summary and that a later session reuses it.
- [ ] Before declaring the batch complete, write the next-step assessment and either update this ExecPlan or create the next dated ExecPlan for the following bottleneck.
- [ ] Continue the plan-update and verification loop until at least two hours of active problem-solving and product validation have been invested in this branch.

## Surprises & Discoveries

The repository is already partway through a hardening cycle on `feat/gstack-review`, and the working tree is dirty with substantial backend, frontend, and documentation changes. That means this plan must assume in-flight work exists and should focus on the next highest-confidence product bottlenecks rather than starting from a blank slate.

The strongest local signal is consistent across `docs/NEXT_PLAN.md`, `docs/PRODUCT_BRIEF.md`, and the existing plans in `plans/`: Murmur should remain education-first on this branch. The next work should not pivot into a broader assistant or a new market narrative. It should make the existing tutoring loop trustworthy and visibly better.

The official Codex guidance aligns with this repo's planning style. Long-running work should keep the main thread focused on requirements, decisions, and final outputs, while bounded subagents handle exploration, tests, or triage. Multi-agent workflows are helpful precisely because they reduce context pollution and keep noisy intermediate work off the main thread.

The official Codex and GPT-5 guidance also reinforces a planning discipline this repo already wants: decompose the work, provide clear preambles for major tool usage, keep explicit progress tracking, and do not stop at partial completion when the user asked for an end-to-end outcome.

The first code trace changed the diagnosis. Chat continuity was not the main structural issue. The voice path was. The `/offer` endpoint did not accept or persist `agent_id`, `_ensure_voice_session()` always created a generic tutor pipeline instead of an agent-backed one, and voice-session finalization did not write `SessionModel.summary` even though cross-session reuse depends on that field. For a voice-first product, that meant the most important path could not participate in durable tutoring memory.

The agent session page on the frontend already had both `agentId` and a persistent `sessionIdRef`, but the WebRTC hook did not send either of them to the backend. The backend and frontend were therefore aligned in the same omission: voice sessions looked live, but they were effectively detached from the persistent tutoring model.

The environment validation changed in two stages. First, `from main import app` was not a meaningful smoke check because the import path was doing too much work: `funcs/__init__.py` eagerly pulled in optional VAD and Smart Turn dependencies, and `main.py` eagerly initialized Smart Turn. After those costs were moved out of import time, the same smoke check completed in Python 3.12 and printed `FastAPI`.

The browser chat path had a quieter but serious product bug. `useChat()` imported the Firebase auth-header helper but did not use it, and it also omitted `agent_id` when calling `/chat`. That meant the text tutoring surface on the session page could fail authentication outright and, even when a session already existed, it was weaker than voice at preserving explicit agent continuity.

The fastest valid local proof path for the authenticated continuity scenario is API-first, not browser-chat-first. The repo already has a real Firebase login flow; the quickest trustworthy loop is to sign in through the existing web login, copy the real bearer token from an authenticated browser request, then drive `GET /api/auth/me`, `POST /api/sessions`, `POST /chat`, and `GET /api/sessions/{id}` directly with that token before doing the full browser voice pass.

## Decision Log

2026-03-27, Codex: This branch will use a living ExecPlan as the primary control surface. The plan must be updated throughout execution, including `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective`, rather than being replaced by ad hoc chat summaries.

2026-03-27, Codex: The first implementation batch should remain education-first and target the highest-confidence product bottleneck already identified by the repo: cross-session context verification, prompt-budget verification, and tutoring-quality validation for physics sessions.

2026-03-27, Codex: Multi-agent work should be bounded and role-specific. The main thread should retain ownership of product decisions, sequencing, and final acceptance, while subagents are used for exploration, validation, test execution, code review, or isolated implementation work with disjoint write scopes.

2026-03-27, Codex: The execution loop for this branch must include a pre-close checkpoint. Before any batch is treated as complete, the agent must inspect what shipped, identify the next bottleneck, and either update this file or create the next dated ExecPlan before continuing.

2026-03-27, Codex: This branch should not be treated as done after a token amount of planning. The minimum operating expectation is at least two hours of active execution, verification, and plan maintenance on the problem unless a hard blocker emerges first.

2026-03-27, Codex: Agent-backed voice sessions must use the same persistent session model as agent-backed chat sessions. For Murmur's current product direction, voice cannot remain a transient peer-only path while chat is the only path that contributes durable tutoring memory.

2026-03-27, Codex: The first code batch should preserve the anonymous canvas page's lightweight behavior. Persistent session creation and cross-session memory should only be activated on the voice path when an authenticated agent-backed session is actually present.

2026-03-27, Codex: The frontend WebRTC offer flow should accept a backend-issued canonical session ID. This removes the race where the session page has created a persistent session in the background but voice connects before the page has committed that ID locally.

2026-03-27, Codex: Import-time work must stay lightweight enough that `from main import app` remains a useful smoke check. Heavy optional dependencies and model initialization should be lazy where possible, especially for package-level exports and Smart Turn.

2026-03-27, Codex: The browser chat path on the agent session page should be treated as a first-class tutoring surface, not a secondary demo. It must authenticate the same way as voice and carry `agent_id` so the persistence and cross-session model stays coherent across modes.

2026-03-27, Codex: Explicitly ending a tutoring session should produce a student-visible recap, not only a backend-side summary row. The minimal acceptable UX is a post-session summary card that confirms what was learned and that future tutoring context was saved.

## Outcomes & Retrospective

The first batch is materially stronger than it was at plan creation, but it is still not fully accepted. Agent-backed voice sessions now have a path to behave like real tutoring sessions instead of ephemeral peer connections. The backend `/offer` flow accepts `agent_id` and `session_id`, validates ownership, creates a persistent session row when needed, returns the canonical session ID to the client, and persists voice-session summaries back onto `SessionModel.summary` when the voice path belongs to a real tutoring session.

The frontend continuity contract is also tighter now. Voice mode forwards `agentId` and `sessionId` through `useWebRTC()` and accepts the backend-issued canonical session ID. Chat mode now attaches Firebase auth headers and `agent_id` to `/chat`, which removes the earlier mismatch where the session page's text mode was not actually using the protected tutoring API correctly.

The student-visible product loop improved as well. Intentionally ending a session from the orb now yields a summary card with the returned recap and mastery-signal count instead of ending silently. That makes the persistence model visible to the student rather than only to the operator.

Verification evidence for this loop is stronger than before:

- `python3 -m py_compile main.py funcs/*.py` passed after the backend changes.
- `cd web && npx tsc --noEmit` passed after the frontend changes.
- `/tmp/voiceai-py312/bin/python -c "from main import app"` now completes and prints `FastAPI`, proving that the import path is viable in a provisioned Python 3.12 environment.
- `/tmp/voiceai-py312/bin/python -m unittest discover -s test -p 'test_authenticated_session_continuity.py'` passed. That test exercises the authenticated persistent-session loop locally with patched auth/LLM boundaries and proves that a later session receives prior-session context from the saved summary.

The main remaining blocker is authenticated end-to-end product proof against the real browser/Firebase boundary. The code paths are improved, the runtime smoke check is now real, and the local integration test proves the backend continuity model. What still remains is one concrete tutoring scenario with a real Firebase-authenticated user and agent: create or resume a session, generate enough tutoring context to matter, end the session, resume it, and confirm that the later tutoring visibly benefits from the saved summary and prior messages.

The next loop should therefore focus on authenticated verification, not more speculative implementation. The fastest trustworthy route is API-first with a real Firebase bearer token copied from the existing browser login flow; after that, the full browser voice path should be exercised to confirm the student-visible continuity loop matches the backend evidence.

## Context and Orientation

Murmur is a voice-first AI tutor that teaches by drawing. In this repository, a "voice session" is the live WebRTC tutoring flow created from `main.py`. A "chat session" is the text tutoring flow handled by the backend chat routes in `main.py`. An "agent" is the configured tutoring persona and capability bundle that is loaded for a session. "Cross-session memory" means summaries, prior conversation context, and related user knowledge being reused when the same tutor is resumed later. "Canvas mode" means the tutor is expected to explain with synchronized visuals, diagrams, or equations rather than plain text alone.

`main.py` is the backend runtime center. It contains FastAPI routes, session maps, WebRTC offer handling, session lifecycle logic, cleanup behavior, and the orchestration points where authentication, session identity, agent loading, memory initialization, and the live tutoring pipeline come together. If session ownership, disconnect cleanup, or `agent_id` propagation is wrong, the root cause is likely visible here.

`funcs/llm_pipeline.py` is the orchestration layer for prompt assembly and LLM execution. It is the place to inspect when cross-session memory is present in storage but does not visibly influence model behavior, or when budget trimming drops the wrong context.

`funcs/memory.py` owns the memory manager, short-term context trimming, summary retrieval, and the assembly of prior-session context into active tutoring prompts. If resumed tutoring does not actually reuse what the student learned earlier, this file is likely in scope.

`funcs/agents.py` compiles the tutor persona and tool/canvas guidance into the system prompt. If Murmur is technically correct but still teaches physics in a generic or visually weak way, this file is where domain-specific pedagogy should be tightened.

`funcs/models.py` backs persistence and repository behavior for sessions, resources, summaries, and related DB interactions. It becomes important if verification shows that memory is not being persisted or loaded correctly.

`funcs/resources.py` controls study-material ingestion and retrieval. It is likely in the second execution batch if uploaded material still does not affect answers strongly enough after the first session/memory validation batch.

`funcs/tts_pipeline.py` becomes part of the next batch if reliability testing shows Murmur still fails too hard when TTS providers error or degrade.

`web/src/` contains the Next.js frontend. `web/src/app/login/page.tsx`, `web/src/hooks/use-auth.ts`, and `web/src/app/obs/page.tsx` are the most likely frontend surfaces for the next branch cycle if operator visibility, auth continuity, or product observability needs to improve.

This branch already contains planning context in `docs/NEXT_PLAN.md`, `docs/PRODUCT_BRIEF.md`, and older ExecPlans in `plans/`. This ExecPlan must stand on its own, but it intentionally continues the product direction those documents already establish: Murmur should become a trustworthy tutoring product before broader positioning or architecture expansion.

## Plan of Work

The first execution batch should prove the tutoring loop rather than invent new scope. Start with the highest-confidence product bottleneck already identified by the repository: session continuity and tutoring quality. Verify that `agent_id` survives the real session path, that a completed or abruptly ended session creates reusable memory, that resumed tutoring loads and uses that memory, and that prompt-budget logic does not erase the most important context under realistic conversations.

Run this batch with a bounded multi-agent operating model. Keep the main thread responsible for the exact product hypothesis, the acceptance criteria, and the final sequence of edits. Use subagents only for clearly scoped tasks such as codebase exploration, static validation, log analysis, isolated implementation in disjoint files, or focused frontend/backend review. This follows the official Codex guidance: multi-agent workflows help most when they keep noisy intermediate work off the main thread and when write-heavy parallelism is used carefully.

If the first batch exposes code gaps, patch only the files that are actually responsible for the failed acceptance path. The most likely files are `main.py`, `funcs/memory.py`, `funcs/llm_pipeline.py`, and `funcs/agents.py`. If the runtime behavior is already correct but hard to prove, add the smallest observability improvement needed to make the product state inspectable by an operator.

Once the first batch is implemented, re-run static validation and a concrete tutoring scenario. Then stop and perform a formal pre-close review inside this ExecPlan. Record exactly what improved, what evidence proves it, what still blocks Murmur from feeling like a very good product, and what should be the next batch. If the next batch is a direct continuation of the same problem, update this plan. If it is distinct enough to deserve a fresh control document, create the next dated ExecPlan and continue on that basis.

The second likely batch, unless runtime evidence changes the priority, is to improve the student-visible quality of tutoring after continuity is proven. That likely means stronger physics-specific visual pedagogy, more reliable use of uploaded learning materials, and TTS fallback behavior that degrades gracefully. Do not commit to this sequence blindly; choose it only after the first batch's evidence is recorded.

This branch should continue in loops of: verify, fix, validate, reassess, update plan, continue. A batch is not done when the code compiles. A batch is done when the product behavior is convincingly better and the next bottleneck has been explicitly captured.

## Concrete Steps

1. Establish the current baseline from repository root.

    Run:
        git status --short --branch
        python3 -m py_compile main.py funcs/*.py
        cd web && npx tsc --noEmit

    Expected result: confirm the current branch and dirty worktree state, then prove the current code still parses and types before any new edits are made for this batch.

2. Verify the first product-critical flow from repository root.

    Exercise one real tutoring scenario using the existing authenticated session path. Create or reuse an agent, start a session with `agent_id`, interact long enough to produce summary-worthy context, close the session explicitly or via disconnect, then start a second session with the same agent.

    Expected result: logs or operator-visible behavior show whether the same `agent_id` is being used, whether summary or prior-session context is loaded, and whether the resumed session visibly benefits from earlier learning context.

3. Inspect the budget and prompt assembly path if the resumed behavior is weak.

    Read the relevant code in:
        main.py
        funcs/llm_pipeline.py
        funcs/memory.py
        funcs/agents.py

    Expected result: identify whether the failure is in session identity, memory persistence, memory retrieval, prompt injection, or budget trimming.

4. Implement the smallest fix set that closes the observed acceptance gap.

    Expected result: only the modules proven to be involved are edited, and the edit is narrow enough to preserve the current branch's stability.

5. Re-run static validation after every meaningful implementation batch.

    Run:
        python3 -m py_compile main.py funcs/*.py
        python3 -c "from main import app"
        cd web && npx tsc --noEmit

    Expected result: backend syntax/import validation and frontend type safety remain intact after each change set.

6. Re-run the same tutoring acceptance scenario.

    Expected result: compare the post-change behavior against the baseline. The change should be visible in logs, prompt assembly evidence, or actual tutoring output rather than inferred from code alone.

7. Perform the pre-close planning checkpoint.

    Update this ExecPlan with:
        - what shipped
        - what evidence proves it
        - what still blocks Murmur from feeling product-complete
        - whether the next work continues in this file or a new dated ExecPlan

    Expected result: the branch remains governed by an up-to-date living plan rather than drifting into undocumented follow-on work.

8. Continue into the next batch and repeat the loop until the branch has accumulated at least two hours of active execution and verification time, unless a genuine blocker prevents further progress.

    Expected result: the project moves through multiple evidence-backed iterations rather than stopping after one planning pass.

## Validation and Acceptance

This ExecPlan is accepted only if it drives a real product-verification loop, not just documentation output. The minimum acceptance for the first batch is one concrete tutoring flow that proves or disproves Murmur's session continuity quality. The minimum evidence should include both static validation and an operator-visible runtime scenario.

The continuity acceptance path is: a student starts a tutoring session with a real agent, generates enough interaction to matter, ends or drops the session, resumes the same tutor, and receives a continuation that visibly benefits from prior context. The plan fails this criterion if memory exists only in storage or logs but does not influence the resumed tutoring behavior.

The prompt-budget acceptance path is: the tutoring flow uses enough turns that budget trimming or memory selection matters, and the retained context still preserves the important educational thread. The plan fails this criterion if the right session is resumed but the meaningful context is trimmed away before it affects the answer.

The product-loop acceptance path is: after the first batch finishes, the ExecPlan is updated with the next bottleneck and the next batch decision. The plan fails this criterion if the code changes are made but the living planning loop is not maintained.

The required static validation commands for any implementation batch remain:

    python3 -m py_compile main.py funcs/*.py
    python3 -c "from main import app"
    cd web && npx tsc --noEmit

If optional dependencies or local environment constraints prevent one of the runtime checks, record that limitation in `Surprises & Discoveries`, provide the strongest fallback evidence available, and immediately state what still needs to be verified in a fuller environment.

The official documentation that shaped this operating model is:

- Codex best practices on long-running session controls and bounded subagent use: https://developers.openai.com/codex/learn/best-practices/#organize-long-running-work-with-session-controls
- Codex slash commands for `/agent`, `/compact`, `/fork`, `/ps`, `/plan`, and related long-run controls: https://developers.openai.com/codex/cli/slash-commands/#built-in-slash-commands
- Codex CLI plus Agents SDK guide for long-running MCP-backed orchestration, guarded hand-offs, and traceable multi-agent workflows: https://developers.openai.com/codex/guides/agents-sdk/#creating-multi-agent-workflows
- GPT-5 prompt-engineering guidance for long-running agentic rollouts, planning, preambles, and TODO-style progress tracking: https://developers.openai.com/api/docs/guides/prompt-engineering/#coding

# Make the Tutor Adapt to What the Student Actually Knows

## Purpose / Big Picture

This ExecPlan covers the next step after the current tutoring-quality batch. The goal is to make the tutor behave less like a stateless explainer and more like a teacher who remembers what the student has already understood, where they struggled, and how to pitch the next explanation.

After this work, a student returning to the same agent should see that the tutor starts from the right prior knowledge. If the student repeatedly struggles with rotational mechanics or graph interpretation, the tutor should surface that context automatically and change how it explains the next problem instead of waiting for the student to restate their history.

Success is observable in one concrete scenario: a student finishes a session where the system records mastery or struggle signals, then starts a later session with the same agent and receives an explanation that explicitly builds from those prior signals. The tutor should proactively revisit weak areas and skip mastered basics unless asked.

## Progress

- [x] 2026-03-23 11:14 IST: Created the ExecPlan and grounded it in the current repository state.
- [x] 2026-03-23 11:22 IST: Audited the current session-end mastery extraction and confirmed that the existing `topic_mastery` table is sufficient for a first mastery-aware tutoring pass.
- [x] 2026-03-23 11:31 IST: Added compact mastery-aware tutoring context injection for agent-backed session startup.
- [x] 2026-03-23 11:34 IST: Strengthened the session-end summary and mastery extraction prompts so later tutoring sessions get more useful signals.
- [x] 2026-03-23 11:36 IST: Completed static verification for the mastery-aware implementation.
- [ ] Verify that repeated sessions with the same agent visibly adapt to prior student performance.
- [ ] Update this document with final runtime evidence once local testing is available.

## Surprises & Discoveries

As of plan creation, the repository already extracts topic mastery signals at session end in `main.py`. The `/api/sessions/{session_id}/end` route generates a free-form summary and then asks a Groq model to emit structured topic entries with `topic`, `chapter`, `signal_type`, and `details`. Those entries are persisted through `TopicMasteryRepo.save_batch()` in `funcs/models.py`.

That means the branch already has the beginnings of structured tutoring memory. The missing part is not data collection. The missing part is using that data at session start so the tutor’s prompt changes before the next explanation begins.

There is already an operator-facing endpoint at `/api/agents/{agent_id}/mastery` in `main.py`, backed by `TopicMasteryRepo.get_summary()` in `funcs/models.py`. That summary is suitable as a starting point for prompt injection and reduces the need for a new schema in the first version of this batch.

The implemented version now adds `TopicMasteryRepo.get_tutoring_context(...)`, which turns existing mastery records into a compact prompt-ready block plus the sorted topic/chapter slices that generated it. That keeps the new tutoring memory deterministic and branch-safe.

The session-start path now uses the shared `append_mastery_context(...)` helper in `funcs/agents.py`, rather than formatting mastery text ad hoc in `main.py`. That avoids multiple prompt formats drifting apart.

## Decision Log

2026-03-23, Codex: This batch will build on the existing `topic_mastery` table instead of introducing a new student-state schema immediately. The current branch already records usable mastery signals, so the fastest path is to reuse them at tutoring time.

2026-03-23, Codex: The first implementation should inject compact mastery context at session start rather than streaming continuous adaptation logic through every turn. Session-start adaptation is smaller, easier to verify, and still delivers visible student value.

2026-03-23, Codex: Free-form session summaries can remain in place for human readability. The structured tutoring signal for this batch should come from mastery records plus a compact synthesized summary, not a large schema migration.

2026-03-23, Codex: This ExecPlan should be executed without unnecessary pauses. Unless a real blocker appears or the user redirects the work, the agent should continue through implementation and static verification rather than stopping after each milestone.

2026-03-23, Codex: The tutoring-context helper should live in `TopicMasteryRepo`, and prompt assembly should reuse the shared `append_mastery_context(...)` helper in `funcs/agents.py`. `main.py` should orchestrate, not own, the mastery-formatting logic.

## Outcomes & Retrospective

Implementation is in. Agent-backed session startup now injects compact mastery-aware tutoring context built from existing topic mastery records, and the explicit session-end prompts now ask for more reusable tutoring signals. Static verification is complete. The only remaining incomplete part is repeated-session runtime proof in a real authenticated environment.

## Context and Orientation

In this repository, a tutoring "agent" is the saved persona/capability configuration loaded into a chat session in `main.py`. A "session summary" is the short narrative summary stored on `SessionModel.summary`. "Mastery data" is the structured set of `TopicMasteryModel` rows stored per user, agent, and session in `funcs/models.py`.

The critical current behavior lives in `main.py` around two places. First, the `/chat` route creates or resumes the tutoring session and builds the `LLMPipeline`. Second, `/api/sessions/{session_id}/end` summarizes the conversation and extracts mastery signals. Today, the tutor can load cross-session summaries, but there is no equivalent injection of mastery-aware teaching context before the next session begins.

`funcs/agents.py` compiles the agent prompt and is the likely place to define how mastery signals should influence teaching style. `funcs/memory.py` and `funcs/llm_pipeline.py` control how context is injected into the prompt. `funcs/models.py` already knows how to aggregate mastery data for an agent via `TopicMasteryRepo.get_summary()`.

This batch should not change frontend routes unless local validation shows that the current session flow does not expose enough state for repeated-session testing. The backend already has the right trust boundary and persistence hooks for a first mastery-aware pass.

## Plan of Work

Start by reading the current session-end intelligence path in `main.py` and the mastery repository code in `funcs/models.py`. The first milestone is to decide exactly what compact student-state block the tutor needs. It should be small enough to fit into the prompt budget and concrete enough to change tutoring behavior. Good candidates are the most recent weak topics, the topics most often marked `struggled`, and the topics most recently marked `understood`.

Once the representation is clear, inject that block into the tutoring prompt for agent-backed sessions. The simplest safe path is to build a concise mastery-aware supplement during session creation in `main.py`, then feed it into `LLMPipeline` or the compiled agent prompt so the tutor starts the session with the right context. The injected block should say what the student struggles with, what they appear to have mastered, and how the tutor should adapt.

After the prompt injection exists, tighten the session-end intelligence path so the data feeding it stays useful. This does not need a heavy schema migration in version one. It can reuse the existing free-form summary and mastery extraction while producing a better compact state for future sessions.

Finish by validating a repeated tutoring scenario. The student should do one session that produces mastery entries, then a later session with the same agent should visibly adapt its response style. The plan is incomplete if the database changes but the tutor’s explanation behavior does not.

## Concrete Steps

1. Establish the baseline from repository root.

    Run:
        python3 -m py_compile main.py funcs/*.py
        cd web && npx tsc --noEmit

    Expected result: the current branch is clean before mastery-aware changes begin.

2. Audit the current mastery extraction path.

    Read:
        main.py around `/api/sessions/{session_id}/end`
        funcs/models.py around `TopicMasteryModel` and `TopicMasteryRepo.get_summary()`

    Expected result: a clear decision on which fields should be injected into the tutor’s next-session prompt.

3. Implement mastery-aware prompt injection.

    Edit:
        main.py
        funcs/agents.py
        funcs/models.py
        optionally funcs/memory.py if the final design fits better there

    Expected result: agent-backed sessions start with a compact student-state block derived from mastery history.

4. Improve the structured tutoring signal at session end.

    Edit:
        main.py
        optionally funcs/models.py if a small repository helper is needed

    Expected result: the session-end path leaves behind cleaner, more reusable data for the next session.

5. Re-run verification from repository root.

    Run:
        python3 -m py_compile main.py funcs/*.py
        python3 -c "from main import app"
        cd web && npx tsc --noEmit

    Expected result: backend and frontend still validate after the change.

6. Run a repeated-session tutoring scenario.

    Use one real authenticated agent-backed flow:
    - Session A: ask several questions, induce one or two clear struggle areas, end the session.
    - Session B: resume with the same agent and ask a related question.

    Expected result: the tutor explicitly adjusts explanation depth or focus based on Session A’s mastery signals.

## Validation and Acceptance

Acceptance for this ExecPlan requires a repeated-session tutoring demonstration.

The minimum acceptance path is:
- Session A produces at least one structured `struggled` or `unclear` mastery signal.
- Session B with the same agent begins with the tutor having access to those signals.
- The tutor’s answer visibly reflects that context, for example by slowing down, choosing a simpler example, revisiting a weak prerequisite, or avoiding repeated basics the student already mastered.

The implementation is not complete if the mastery data is only visible through the `/mastery` API but does not affect the tutor’s actual prompt or behavior.

Minimum verification commands:

    python3 -m py_compile main.py funcs/*.py
    python3 -c "from main import app"
    cd web && npx tsc --noEmit

If the local runtime environment cannot run the backend import smoke test because the virtualenv is outdated or missing dependencies, record that in `Surprises & Discoveries` and still provide the strongest repeated-session evidence available.

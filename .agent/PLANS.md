# Codex Execution Plans for `voiceai`

This document defines how to write and maintain an execution plan, called an ExecPlan, for this repository. An ExecPlan is a self-contained design and implementation document that a coding agent or new contributor can follow without relying on prior chat history or outside context.

## How to use this file

When authoring an ExecPlan, follow this document exactly. Re-read it before creating a new plan if it is not already in context. When implementing from an ExecPlan, keep the document updated as work progresses instead of treating it as a static proposal. If design decisions change, record that change in the plan itself.

In this repository, ExecPlans should live in `plans/` and should be named with a date and short slug. Example: `plans/2026-03-23-physics-prompt-upgrade.md`.

## Non-negotiable requirements

Every ExecPlan must be fully self-contained. A reader should be able to understand the problem, locate the relevant files, run the commands, and verify the result without opening any other planning document.

Every ExecPlan must describe a user-visible or operator-visible outcome. If the change is internal, the plan must still explain how to prove it worked by running tests, inspecting logs, or exercising a concrete scenario.

Every ExecPlan must be a living document. At minimum, it must always contain and maintain `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective`.

Every ExecPlan must be execution-oriented. Unless the user explicitly pauses, redirects, or a real blocker is encountered, the implementing agent should continue working the plan without stopping at intermediate milestones just to ask whether to proceed.

Every ExecPlan must define project-specific terms in plain language when they first appear. In this repository, examples include "voice session", "chat session", "agent", "cross-session memory", "Smart Turn", and "canvas mode".

Every ExecPlan must name files precisely with repository-relative paths and describe the exact functions, modules, routes, or components to edit.

## Writing style

Write in plain prose. Prefer sentences over bullet-heavy outlines. Use checklists only in `Progress`, where checkboxes are mandatory.

Resolve ambiguity in the plan instead of pushing decisions to the reader. If you choose one approach over another, explain why.

Keep milestones narrative and testable. Each milestone should state what will exist after it finishes, what commands to run, and what acceptance looks like.

If you include command output, diffs, or logs, keep them short and focused on evidence. In a standalone ExecPlan file, do not nest code fences inside the plan body. Indent command examples instead.

## Required sections

Every ExecPlan must contain these sections in this order unless there is a strong reason to add more:

`# <Short action-oriented title>`

`## Purpose / Big Picture`
Explain what changes for a real user or operator and how they will observe success.

`## Progress`
Use timestamped checkboxes. This section must reflect reality at every stopping point.

Example format:
- [x] 2026-03-23 10:30 IST: Verified current session lifecycle and auth flow.
- [ ] Implement DB-backed session ownership checks in `main.py`.
- [ ] Validate resumed `agent_id` memory injection end to end.

`## Surprises & Discoveries`
Record unexpected findings with short evidence.

`## Decision Log`
Record decisions, rationale, and date/author.

`## Outcomes & Retrospective`
Summarize what shipped, what remains, and what was learned.

`## Context and Orientation`
Explain the relevant parts of the repository for a newcomer. Name the important files and how they connect.

`## Plan of Work`
Describe, in prose, the intended sequence of edits and why that order is safe.

`## Concrete Steps`
List the exact commands to run, with working directory, and the expected observable result.

`## Validation and Acceptance`
Describe how to prove the change works, not just that it compiles.

## Repository-specific guidance

This repo has a few central surfaces that many plans will need to orient around:

- `main.py` contains the FastAPI routes, session maps, WebRTC offer handling, and much of the runtime lifecycle.
- `funcs/` contains the backend modules for config, auth, memory, LLM orchestration, tools, models, and persistence.
- `web/src/` contains the Next.js frontend, including auth hooks, route structure, and the canvas/chat UI.
- `docs/` contains product and engineering context, but an ExecPlan must not assume the reader has loaded those docs.

If an ExecPlan touches both memory and sessions, explain how `main.py`, `funcs/llm_pipeline.py`, `funcs/memory.py`, and `funcs/models.py` fit together before giving edit instructions.

If an ExecPlan changes auth or trust boundaries, explicitly describe the expected request path, how user identity is derived, and how misuse or mismatches should fail.

If an ExecPlan changes frontend routes or auth flows, name the route files under `web/src/app/` and the hooks or providers involved.

## Validation commands for this repository

Use the commands that match the files touched, and include them in the ExecPlan with working directory:

Backend syntax validation from repository root:
    python3 -m py_compile main.py funcs/*.py

Backend import/startup smoke check from repository root:
    python3 -c "from main import app"

Frontend typecheck from repository root:
    cd web && npx tsc --noEmit

If an ExecPlan changes a live route or auth/session behavior, include at least one end-to-end scenario beyond static checks. Examples include:
- a `curl` request with the exact headers and expected HTTP status
- a WebRTC or chat flow described step by step
- a browser verification path for a frontend route

If a command is expected to fail in some developer environments because an optional dependency is missing, state that assumption and provide the fallback validation the reader can still run.

## Prototypes and parallel paths

Prototyping milestones are acceptable when they reduce uncertainty. Label them clearly as prototypes, describe how to run them, and state what evidence will decide whether to keep or discard them.

Parallel implementations are also acceptable during migration work when they reduce risk, but the ExecPlan must say how both paths are validated and how the old path will eventually be retired.

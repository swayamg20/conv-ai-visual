# AGENTS.md

## ExecPlans

When writing complex features or significant refactors, use an ExecPlan from design through implementation, as described in `.agent/PLANS.md`.

Use an ExecPlan when the work is likely to take more than about an hour, crosses backend and frontend boundaries, changes auth or session behavior, touches memory or persistence, changes the voice pipeline, or has enough uncertainty that research or prototypes are part of the task.

Do not use an ExecPlan for small, local fixes that can be completed and verified in one short pass.

Store live ExecPlans in `plans/` using a dated filename such as `plans/2026-03-23-session-memory-verification.md`.

An ExecPlan is a living document. Keep `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` current as work proceeds. If `.agent/PLANS.md` is not already in context, read it again before writing or executing a plan.

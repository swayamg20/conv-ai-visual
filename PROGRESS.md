# PROGRESS — Relay Invocation Process Adapter

## Session log

- 2026-10-26: Prior sessions shipped Checkpoint 1 complete (`e324eb6`, `8e69821`, `96c14de`) and the first two effect-free Checkpoint 2 slices (owner foundation `f539265` admission/lifecycle substrate; video side-plan docs `3167068`). Branch `codex/pipecat-relay-b0` synced with origin at `3167068`.
- 2026-10-26: This session began the final Checkpoint 2 boundary: registered-before-initialization spawn, child-bound readiness, finish/stop, quarantine, deterministic fault tests. Created `PLAN.md` and this file. Recon and design in progress (see PLAN.md Active task).

## Completed this session

- [x] PLAN.md / PROGRESS.md created.
- [x] 2026-10-26: Repaired the unfinished local spawn seam to reuse `registered_popen_factory`, registering the raw child before initialization and setting the child-only `umask=0o077`. Removed the callback/list mismatch and process-wide umask mutation. Added positive PID checks, distinguished unavailable WNOWAIT from a running child, validated the observed PID, made reap nonblocking, and rejected unreadable/malformed `/proc` evidence. Loopback HTTP now verifies Host and peer and rejects redirects and oversized responses.
- [x] 2026-10-26: Added fake-only regression tests for registration ordering, initialization return loss (ordinary/control exceptions), unavailable/wrong-PID WNOWAIT, invalid signal targets, unreadable process evidence, and HTTP response failures. Local + existing lifecycle + concrete integration suites: 125 passed with warnings as errors. Scoped Ruff lint/format passed. The adapter callbacks remain inert; this does not complete Checkpoint 2.
- [ ] Independent review blocked: two reviewer Task calls completed with empty outputs, so neither supplied a verdict. Review must be rerun before accepting this slice.

## Remaining tasks

- [ ] Tk-A: Parallel recon (spawn seam contract, lifecycle callback surface, state authority shapes) — research only.
- [ ] Tk-B: Implement `process_local.py` OS/loopback seam.
- [ ] Tk-B: Implement `process_lifecycle.py` role state machine (start/readiness/finish/stop/quarantine, publication through existing destinations).
- [ ] Tk-B: Implement `process.py` private driver/tools factory (`__all__ = []`) adapting existing callback signatures.
- [ ] Tk-C: Deterministic fake-seam tests + phase-cut fault matrix for all three roles.
- [ ] Tk-D: Reviewer agent on diff; fix critical/major findings.
- [ ] Tk-D: Gates: focused `-W error` suites, adjacent executor/driver/owner suites, full repo suite, Ruff lint/format, `py_compile`, module <700 lines, `git diff --check`.
- [ ] Tk-D: Update ExecPlan (`plans/2026-08-29-relay-invocation-process-adapter.md`) Progress/Surprises/Decision Log/Outcomes; update PLAN.md + this file; commit + push.
- [ ] After Checkpoint 2 lands: Checkpoint 3 (executor binding + reverse-cleanup proof via fake seams), Checkpoint 4 (gates + independent review), per ExecPlan. Checkpoint 5 remains separately authorized (out of scope autonomously).

# PROGRESS — Relay Invocation Process Adapter

## Session log

- 2026-10-26: Prior sessions shipped Checkpoint 1 complete (`e324eb6`, `8e69821`, `96c14de`) and the first two effect-free Checkpoint 2 slices (owner foundation `f539265` admission/lifecycle substrate; video side-plan docs `3167068`). Branch `codex/pipecat-relay-b0` synced with origin at `3167068`.
- 2026-10-26: This session began the final Checkpoint 2 boundary: registered-before-initialization spawn, child-bound readiness, finish/stop, quarantine, deterministic fault tests. Created `PLAN.md` and this file. Recon and design in progress (see PLAN.md Active task).

## Completed this session

- [x] PLAN.md / PROGRESS.md created.
- [x] Local seam checkpoint pushed (`e0ea340`): registered spawn (child-only umask), fail-closed PID/WNOWAIT//proc checks, bounded POST-capable loopback transport.
- [x] Settlement engine (`process_lifecycle.py`): intend/start/terminal-zero/stop/quarantine with TERM-grace KILL from stored timestamp; 11 fake-seam tests.
- [x] Dispatch activation (`_concrete_forward` replacing `_inert_forward`): start / browser finish / app prebootstrap (bounded loopback POST, live-app gate both sides, exact-schema publish); 17 dispatch tests incl. fault matrix (RuntimeError/KeyboardInterrupt/SystemExit never publish).
- [x] Stop path activation: started-role settled outside all locks term-then-kill, revalidated post-settlement before graph marking; unstarted path unchanged (68 integration tests still green).
- [x] Fixed test monkeypatch leak (dispatch tests now restore patched attributes) and lock-across-effects bug found in self-review.
- [x] Gates: 266 focused `-W error`, 443 adjacent, 3,859 full-repo tests, Ruff lint/format, `py_compile`, module caps <700, `git diff --check`.

## Completed earlier this session

- [x] Settlement slice committed + pushed (`4594478`).
- [x] Readiness digest contract: guarded app nonce env (64-hex, relay-only) + `process_ready_digest` on `/_e2e/health`; web `/_ready` force-dynamic route mirroring the same domain-separated digest; `process_readiness.py` polls per-role loopback endpoints with exact-digest and live-child gating; foreign 200s/missing nonce/dead child/expired deadline never publish.
- [x] Gates: 278 focused `-W error`, 3,871 full-repo, web 298 vitest + typecheck + lint + build, Ruff, module caps.
- [x] Parallel Cosmo lane spawned detached (see var/opencode-sessions/cosmo-lane.log): snapshotted the lab (`15da840` on `codex/cosmo-voice-lab`), rebasing onto main, then workspace cascade discovery.

## Remaining tasks

- [ ] Commit + push the readiness slice (staged below).
- [ ] Cosmo lane: monitor to completion (rebase + discovery + docs).
- [ ] Executor-level per-cut integration (fake local seams through the real outer executor) — Checkpoint 3.
- [ ] Independent no-P0/P1 reviewer gate (subagent empty so far; retry or user sign-off).
- [ ] Checkpoints 3-4 per ExecPlan; Checkpoint 5 remains separately authorized.

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

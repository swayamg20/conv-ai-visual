# PLAN — Relay Invocation Process Adapter (Checkpoint 2 completion + onward)

This is the working plan for finishing the Pipecat forced-relay qualification path in
`plans/2026-08-29-relay-invocation-process-adapter.md` end to end with taste. It tracks
only the live work in flight. The authoritative ExecPlan remains
`plans/2026-08-29-relay-invocation-process-adapter.md`.

## Active task: implement the process-local OS/HTTP seam + process lifecycle state machine

Implement the remaining Checkpoint 2 boundary: **registered-before-initialization spawn,
child-bound readiness, finish, stop, quarantine, and deterministic fault tests** on top
of the admitted process-owner graph (which shipped as `f539265`).

1. **Research (parallel, read-only)** — three explore agents:
   - `registered_popen_factory` / Coturn subprocess seam contract (spawn registration, PID/PGID checks, TERM/KILL escalation, reap, group-absence).
   - Existing invocation lifecycle (`voice_pipecat_e2e_relay_invocation_lifecycle.py`) startup path — app start / prebootstrap (readiness transport contract) / web start / browser start & finish — and destination publication protocol.
   - Process state authority shapes (`process_state.py`, `process_owner_*.py`, `process_values.py`, role authority, state conditions, cap-one registry, total-absence predicate).
2. **Design** — folder-by-authority output: `process_local.py` (spawn/inspect/signal/observe/reap/HTTP seam), `process_lifecycle.py` (role state-machine transitions), `process.py` (driver/tools factory adapting callback signatures; `__all__ = []`), split by lock precedence so adapter locks are never held across effects. `umask=0o077`, `start_new_session=True`, `shell=False`, `close_fds=True`, `DEVNULL` stdio, `executable=argv[0]`, `weak authorization through role authorities only`.
3. **Implement production modules** — plan target: registered-before-init spawn via `registered_popen_factory()`, non-reaping `waitid(..., WNOWAIT)` terminal observation, live-descendant proof before reap, final group-absence check, term/kill only while freshly-proven-live `pid==pgid` leader, bounded child-bound readiness (digest-proven) via literal-`127.0.0.1` proxy-disabled transport, prebootstrap reuse of authenticated destination, finish/stop publication through existing destinations, quarantine on ambiguous facts (no speculative signals), dual-deadline (runtime vs cleanup) enforcement.
4. **Tests** — deterministic fake-seam factory tests plus phase-cut/fault matrix (`error` / `KeyboardInterrupt` / `SystemExit(71)` at every effect/commit boundary), proving: fail-closed unknowns, no duplicate spawn, WNOWAIT-before-reap-before-descendant-proof ordering, no signal after terminal-observed leader (signal revocation), no publish on ambiguous/foreign readiness, no success receipt for nonzero exit or alive-descendant cases, first-control escape authoritative across synchronously-rolled-back cuts, deadline phase separation (runtime never authorizes cleanup and vice versa), reuse-permitting cleanup after total absence, secrets never in runtime identifiers.
5. **Check then land the Capstone (if time and stability allow)** — keep modules below 700 lines, Ruff clean, full "test/review/push" gates follow `.agent/PLANS.md` and the ExecPlan's Concrete Steps section.
6. **Checkpoint 3–5** — proceed per the ExecPlan after this boundary is green: bind the concrete adapter into the real executor behind the concrete selection; run all gates, independent reviews; then executor integration with reverse-cleanup proof (fake seams); then gates/review/push. (Checkpoint 5 — disposable Linux/amd64 forced-relay qualification run — remains separately authorized and out of scope for autonomous execution.)

## Task states

- Tk-A: direct recon performed; delegated recon returned empty outputs.
- Tk-B: local seam draft repaired; lifecycle, readiness contract, provenance binding and callback activation remain incomplete.
- Tk-C: local seam regressions added and passed; lifecycle phase-cut/overlap coverage remains incomplete.
- Tk-D: scoped Ruff checks and 125 strict local/lifecycle/integration tests passed. Two independent reviewer attempts returned empty outputs; review remains blocked. Full gates and landing remain pending.

Explicit exclusions: anything Checkpoint 5 or Docker/TLS/Coturn-image qualification; public surface changes to `scripts/voice_pipecat_e2e_stack.py`; new bash/toml/env values not owned by this work.

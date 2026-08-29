# Own Concrete Relay Invocation Processes After Build Consumption

## Purpose / Big Picture

The private Pipecat forced-relay qualification path currently composes every major owner needed for a disposable Linux run, but its app, web, and browser children are still represented by synthetic callbacks. This plan replaces only that synthetic process seam with a real, privately constructed process adapter. An operator will be able to run the already-built private executor and know that the app was started from the provenance-bound repository source and Python executable, the web and browser were started from the consumed web workspace and pinned Node toolchain, each became ready as its exact owned child, and every child finished or stopped without leaving a process group or adapter authority behind.

This plan deliberately does not make `--network relay-tls` public or claim that relay media has qualified. The public CLI must continue to return the fixed refusal before generating a run ID, touching a path, creating a registry, opening HTTP, or starting a process. The concrete adapter remains private and its observations remain falsey and non-qualifying until a separately authorized disposable Linux/amd64 run proves the pinned Coturn grammar, `/29` topology, TLS trust, forced-relay candidate pair, bidirectional media bytes, browser result, and total cleanup.

In this plan, a **consumed build** is the exact one-shot built-workspace lease already transferred to the private outer executor. A **concrete invocation adapter** is the private capability that owns the app, Next server, and Playwright processes. **Return loss** means an effect completed but its Python call raised or control escaped before the caller saw the result. **Readiness** means both that the expected endpoint returned the exact response and that the response was cryptographically tied to the still-live owned child. **Quarantine** means cleanup authority is retained because process absence cannot yet be proven; quarantined state cannot publish success or release capacity.

The observable development outcome is a fake-process integration that traverses the real private executor in this order and returns only after every named registry is empty:

    outer preowned
      -> workspace prepared, built, and consumed
      -> concrete invocation grant minted
      -> app, web, and browser authorities preowned
      -> app started and child-bound readiness proved
      -> existing prebootstrap and relay owners run
      -> web started and child-bound readiness proved
      -> browser started, finished at zero, reaped, and group-absent
      -> existing browser result consumed
      -> browser, web, and app stopped in reverse order
      -> invocation adapter absent
      -> inner, build, workspace, ports, and outer owner absent
      -> falsey `qualification_verified=False` observation returned

## Progress

- [x] 2026-08-29 01:35 IST: Re-read `.agent/PLANS.md`, confirmed branch `codex/pipecat-relay-b0` was clean and exactly synchronized at `8b728fe`, and mapped the consumed-build, invocation, process, and public-refusal boundaries.
- [x] 2026-08-29 01:35 IST: Completed independent architecture, security, and test audits. All three agreed that `_ManagedProcess` is unsafe here, concrete capability construction must occur only after durable build consumption, and the public relay refusal must remain unreachable from the adapter.
- [x] 2026-08-29 01:35 IST: Wrote this dedicated living ExecPlan and fixed the authority graph, state machine, lock order, readiness proof, phase-cut matrix, and scope boundaries before implementation.
- [x] 2026-08-29 01:43 IST: Incorporated two read-only landing reviews of the actual plan: separated forward and cleanup deadlines, revoked signal authority after leader termination, disabled HTTP proxies and redirects, corrected source/workspace and symlink claims, specified selector-versus-effective-pair replay, narrowed secrecy to outward/terminal state, and added post-readiness app/web death gates.
- [x] 2026-08-29 01:56 IST: Implemented the effect-free Checkpoint 1 substrate: an inert canonical concrete-selection singleton, optional exact child start deadlines, and a private pair-bound stop deadline request. The 83 invocation/value tests and 126 public stack tests pass with warnings promoted to errors; no executor admission, concrete pair, registry, nonce, filesystem, process, HTTP, browser, or media effect was added.
- [x] 2026-08-29 03:29 IST: Completed the consumed-build execution-provenance prerequisite for Checkpoint 1. One private immutable runtime proof now retains the exact prepared baseline, final output snapshots, `node_modules` link identity, and Node/Next/Playwright/package identities plus digests through built publication and exact executor consumption. Cleanup converts that proof to digest/process-only retirement evidence and releases every proof-owning registry. Adversarial coverage rejects wrong owner, record, digest, shape, identity, same-content Playwright replacements, malformed stored state, and proof swaps at genuine settled-inner release and revocation-acknowledgment cuts. Two independent final reviews found no P0-P2 issue. Root verification passed 481 adjacent invocation/build/executor tests and 126 public stack/refusal tests with warnings as errors plus Ruff, formatting, byte-compilation, diff, and module-cap checks. This checkpoint still mints no concrete pair and enables no filesystem read, process, HTTP, browser, Docker, network, or media effect beyond the already-existing private build transaction.
- [ ] Checkpoint 1: add failing boundary tests, consumed-build invocation provenance, and sealed matched concrete driver/tools construction with no OS or HTTP effects.
- [ ] Checkpoint 2: add the cap-one app/web/browser process owner, registered-before-initialization spawn, child-bound readiness, finish, stop, quarantine, and deterministic fault tests.
- [ ] Checkpoint 3: bind the concrete adapter into the existing private executor after consumption and prove reverse cleanup plus total registry/path/port absence through fake local seams.
- [ ] Checkpoint 4: run focused, adjacent, full-suite, static, public-refusal, and Linux process-group conformance gates; obtain an independent P0/P1 review; commit and push each complete checkpoint.
- [ ] Checkpoint 5: after separate authorization, run one disposable Linux/amd64 forced-relay qualification and record the exact positive or negative evidence without changing the public refusal on a partial result.
- [ ] Close this plan with exact shipped commits, test counts, known guarantee limits, and the next Milestone 1B action in `Outcomes & Retrospective`.

## Surprises & Discoveries

The existing executor already creates the runtime deadline only after `_RelayLinuxExecutorBuiltEvidence` is confirmed consumed. However, the executor currently receives `RelayInvocationDriver` before that point and `_new_workspace_invocation_tools()` derives Next and Playwright paths with path arithmetic. A concrete adapter must therefore be selected by a sealed inert mode before consumption but created only from the exact live consumed evidence afterward. The factory must never be invoked on a pre-consume failure.

The executor already performs intentional workspace preparation and a Next build before consumption, so “zero effects before consumption” cannot describe the whole private executor. The enforceable rule is narrower: the new concrete selection may exist, but it cannot call the concrete factory, create adapter-local state, mint nonces, or perform process/HTTP effects until the built lease is consumed. The public refusal retains the stronger global zero-effect rule.

The current invocation lifecycle intentionally rejects `concrete_adapter=True`. That is a useful guard to preserve. Flipping the flag, subclassing the synthetic driver, or attaching real callbacks to the public synthetic factory would let arbitrary callbacks masquerade as a concrete owner. Concrete construction needs a second hidden seal and a private registry-backed match between driver, tools, consumed grant, outer executor, deadline, and clock domain.

The old `_ManagedProcess` in `scripts/voice_pipecat_e2e_stack.py` registers a process only after `Popen` returns. It can lose a child if `Popen.__init__` created the child and the call then failed or control escaped. It also treats an exited leader as stopped without proving descendants in the process group are absent. The adapter must instead reuse the `registered_popen_factory` pattern and the existing build/Coturn group settlement rules.

Port-level HTTP readiness is not ownership evidence. Another server can answer the fixed app or web port. The existing app health schema has useful runtime, profile, network, provider, and topology fields, but it lacks a fresh child-bound value. The concrete path therefore needs a private per-role nonce injected through the exact replacement environment and a bounded readiness response containing only its digest. Process liveness and identity must be checked immediately before and after the response. The Next side needs a force-dynamic private E2E health route for the same purpose; a generic nonempty page response is insufficient.

Literal loopback URLs are still unsafe if the HTTP library honors ambient proxy variables or follows redirects. App readiness, web readiness, and authenticated prebootstrap must use a DNS-free `127.0.0.1` endpoint through a transport object with proxies disabled, redirects disabled, an exact method and Host header, bounded bodies, and loopback peer validation where the standard-library seam exposes it. A hostile `HTTP_PROXY`/`HTTPS_PROXY` environment must never see the request or Authorization header.

Playwright may exit while a Chromium descendant remains. A zero leader return code is not a successful finish until terminal state is observed without reaping, live descendants are proven absent, the leader is reaped, every owned handle is closed, the raw destination is cleared, and the exact process group is finally absent. The same rule applies to stop. If the leader is already terminal and descendants remain, signal authority is gone and the role is quarantined.

An expired forward deadline cannot also be the cleanup budget. The executor already distinguishes runtime and cleanup timeouts. The concrete path needs one immutable forward deadline for spawn/readiness/prebootstrap/finish and one separately minted bounded cleanup deadline when cleanup first latches. Cleanup expiry retains quarantine and capacity; it never authorizes more forward work or silently refreshes during a retry.

The current child request already fixes `output_policy` to `discard`, which is the correct concrete policy. The adapter should use `DEVNULL` and never create raw app, web, or browser log files. The existing browser-result and Coturn evidence owners remain the only artifact producers in this path.

The build lease previously retained only the final output digest. That was enough to bind cleanup and replay, but not enough to authorize later app/web/browser starts from the exact worker-observed source, output, symlink, and toolchain. The lease now retains one exact private runtime-proof object until retirement. A final adversarial review also exposed two easy-to-miss rules: stored malformed proof state must be inspected through proof-owned fail-closed accessors rather than `proof.output.digest`, and cleanup must rebind the current consumed or revoked lease to the exact retained proof identity before releasing use or acknowledging revocation. Digest equality alone is cleanup evidence, not live execution authority.

The practical threat boundary is cooperative Linux with a trusted repository checkout, a live CPython interpreter, finite handled `KeyboardInterrupt`/`SystemExit`, and same-UID code that does not actively race descriptor-backed validation. Path and digest revalidation cannot defend against a hostile same-UID process indefinitely. A stronger claim would require a sealed read-only mount, namespace, pidfd/cgroup containment, or a container-level parent-death contract and is outside this checkpoint.

## Decision Log

2026-08-29, Codex: Keep the exact nominal `RelayInvocationDriver` and `RelayInvocationTools` types so `RelayInvocationOwner` and its tested destination protocol do not fork. Add separate hidden synthetic and concrete construction seals. Admit only matched synthetic/synthetic or concrete/concrete pairs, and require a private canonical registry match for the concrete pair. Derive `RelayInvocationOwner.concrete_adapter` and its safe `repr` from that canonical matched pair so they are truthful; the boolean remains descriptive and never authorizes anything.

2026-08-29, Codex: Preserve the current caller-facing synthetic executor path for its deterministic tests. Add one sealed inert concrete selection understood only by the private executor. The selection can cross pre-consume code because it owns no per-run state and can perform no effect; the consumed-build grant, concrete tools, driver, nonce, directories, and process authorities are minted only after `_consumed_binding_matches()` and `_cleanup_evidence_matches()` both pass.

2026-08-29, Codex: Keep caller selection and effective invocation identity separate. Outer driver/attempt/terminal replay state retains only the inert selector. Inner live evidence retains selector plus the effective matched pair. A caller-preowned pair destination and canonical grant/pair registry exist before factory entry after consumption, so factory return loss rereads the same pair rather than minting again. Terminal cleanup revokes and scrubs the live pair and callbacks before observation, then retains only the non-authoritative selector tombstone needed for same-input replay.

2026-08-29, Codex: Extend the consumed-build execution anchor before spawning. Bind and revalidate descriptor-derived identity and digest for the Python executable and E2E app entrypoint, original Node executable, consumed `node_modules` target, Next CLI and package metadata, Playwright CLI and package metadata, Playwright configuration and exact RTC spec, lockfile, and the run-specific validated Next output. The one intentional `workspace/node_modules` symlink is required with its exact link identity and absolute target-tree provenance. Reject any changed or additional symlink, swap, rename, hardlink, owner/mode/link-count change, or digest mismatch before every role start.

2026-08-29, Codex: Use one immutable monotonic `runtime_deadline` created by the executor for forward spawn, readiness, prebootstrap, and browser finish, with browser finish clipped to the earlier of its request deadline and the runtime deadline. Retain the already validated cleanup timeout and same clock domain in the concrete `RelayInvocationOwner`/adapter cleanup authority. When `_cleanup_invocation_locked()` first changes that owner to cleanup-required, atomically mint and store one distinct `cleanup_deadline` before its synchronous rollback can call any stop callback, then pass it through an exact stop request to browser, web, and app. Later `RelayProbeOwner` and executor cleanup may read or consume that deadline but are never prerequisites for minting it. Their unrelated resources may retain their existing separate cleanup episodes. No deadline refreshes within its episode, and no cleanup deadline can authorize forward progress.

2026-08-29, Codex: Use one cap-one adapter with three preowned role slots. The adapter owns app, web, and browser authorities before the first spawn. Each authority is bound to the consumed grant, adapter token, role, and generation. Same exact replay is idempotent; a changed grant, role, destination, request, clock, deadline, or command is a contradiction and enters cleanup-only handling.

2026-08-29, Codex: Separate start, readiness, finish, and stop facts. App and web start destinations receive publication only after child-bound readiness. Browser start publishes after registered PID/PGID ownership and a fresh running check. Browser finish publishes only after zero exit, no live descendants, reap, handle closure, raw clearing, and group absence. Stop may signal a process group only while the leader is freshly proven live, unreaped, and still has `pid == pgid`; revalidate that fact before TERM and again before any KILL. Terminal or reaped leader state, identity drift, or ambiguous signal observation permanently revokes signal authority. If descendants remain then, observe and quarantine rather than signaling a remembered PGID. Stop publishes only after terminal absence proof. Ambiguity retains quarantine authority and cannot release capacity.

2026-08-29, Codex: Use one private literal-loopback HTTP transport with proxy lookup disabled and redirects rejected for readiness and prebootstrap. Require exact method, URL, Host, response schema, response cap, and direct loopback peer where observable. Ambient proxy and no-proxy variables are ignored rather than trusted.

2026-08-29, Codex: Keep the public `relay-tls` refusal byte-for-byte and add static and dynamic regressions proving it cannot import or call the concrete factory, registered `Popen`, HTTP, UUID, filesystem, Docker, or browser seams. No environment flag or public constructor may activate the private adapter.

2026-08-29, Codex: Do not generalize the hardened Docker/OpenSSL subprocess supervisor or refactor the build-process subsystem in this plan. Reuse their low-level registration and process-group ideas through adapter-specific modules so their already-reviewed threat boundaries and tests remain unchanged.

2026-08-29, Codex: Retain one exact `_WorkspaceBuiltRuntimeProof` from successful worker validation through built-lease consumption. The proof owns the prepared baseline, final output evidence, and six named tool-file identity/digest pairs; executor evidence must reference the same object, not a reconstructed equivalent. Live consumed and revoked transitions require that exact identity. Retirement deliberately drops it and keeps only the existing digest/process facts so terminal state cannot authorize execution. All self-derived digest reads go through fail-closed proof methods, including poisoned or uninitialized exact-type state. Release and revocation acknowledgment live in a separate focused module so the consume module remains comfortably below the 700-line cap.

## Outcomes & Retrospective

The first effect-free runtime substrate shipped as `e324eb6`: the private inert selector and immutable deadline-bearing request values exist, but the executor still rejects concrete operation. The next Checkpoint 1 slice now adds the exact consumed-build runtime proof needed by the future execution anchor. It still creates no concrete pair, child, readiness request, browser, Docker, network, or media observation. The implementation boundary remains capability and provenance first, process ownership second, executor integration third, and real disposable-Linux qualification only after explicit authorization. It rejects flag flipping, `_ManagedProcess` reuse, fixed-port readiness, digest-only live authority, and leader-only exit checks.

Update this section after every pushed checkpoint with the commit SHA, files changed, exact focused and full-suite counts, independent review verdict, and the strongest truthful claim. A synthetic or fake-process pass must continue to say that no real child, Docker, network, browser, service, or media qualification occurred. A Linux process-group conformance test may prove POSIX ownership behavior, but it is still not relay/media qualification.

## Context and Orientation

`scripts/voice_pipecat_e2e_relay_linux_executor_driver.py` is the caller-preowned outer transaction. It prepares the workspace worker, runs the build, consumes the built lease, invokes the inner relay owner, and withholds the observation until reverse cleanup proves the entire outer graph absent. Its existing input includes a synthetic invocation driver before build consumption; that stays valid for deterministic tests.

`scripts/voice_pipecat_e2e_relay_linux_executor_inner_contract.py` is the first safe concrete construction seam. `_resolve_or_intend_inner_evidence()` confirms the exact `_RelayLinuxExecutorBuiltEvidence`, consumed binding, cleanup evidence, runtime timeout, cleanup timeout, and clock before it creates `runtime_deadline`, runtime paths, and invocation tools. The new concrete grant and capability pair must be created here, after those checks, not in the public CLI or caller. The validated cleanup timeout is retained without starting its clock; the concrete invocation cleanup authority converts it to one absolute deadline when its own cleanup transition first latches, including synchronous rollback inside a failed start/readiness/prebootstrap/finish call.

`scripts/voice_pipecat_e2e_relay_invocation_driver.py` defines the exact driver and tools capability shapes plus preowned child destinations. `scripts/voice_pipecat_e2e_relay_invocation_values.py` defines immutable child and finish requests and the falsey Playwright exit receipt. `scripts/voice_pipecat_e2e_relay_invocation_support.py` derives the exact app, Next, and Playwright commands and replacement environments. `scripts/voice_pipecat_e2e_relay_invocation_lifecycle.py` stages app, prebootstrap, web, and browser operations. `scripts/voice_pipecat_e2e_relay_invocation_cleanup.py` stops browser, web, and app in reverse order. Their generic destination and replay protocol should be preserved.

`scripts/voice_pipecat_e2e_relay_owner_forward.py` already sequences the invocation owner with the `/29`, generated TLS, Coturn container/runtime, evidence drain, username adoption, relay browser authorization, and browser-result owner. `scripts/voice_pipecat_e2e_relay_owner_cleanup.py` already performs aggregate reverse cleanup. The adapter must fit behind these owners instead of rebuilding topology, TLS, Coturn, evidence, or artifact logic.

`scripts/voice_pipecat_e2e_coturn_subprocess_spawn.py` contains `registered_popen_factory()`, which publishes the raw `Popen` object before `Popen.__init__` can create a child. `scripts/voice_pipecat_e2e_coturn_subprocess_process_io.py` and the build-process modules demonstrate PID/PGID validation, TERM/KILL escalation, reap, handle closure, and exact group-absence checks. They are references for behavior; the new adapter remains role-specific.

`scripts/voice_pipecat_e2e_app.py` exposes `/_e2e/health` and the authenticated relay prebootstrap endpoint. The concrete readiness extension must preserve existing direct and synthetic callers while optionally returning a digest of a factory-owned process nonce only when the sealed concrete environment is present.

`web/src/app/e2e/voice/` is the guarded browser page. Add a server-only, force-dynamic readiness route beneath this tree that returns a bounded exact schema and the digest of the concrete web nonce. It must not expose the nonce itself, import browser-only code, or change production routes. The validated consumed build must include this route before the adapter can rely on it.

The new private production modules are:

* `scripts/voice_pipecat_e2e_relay_invocation_process_values.py` for the falsey, immutable, noncopyable, nonserializable consumed grant, adapter/role authorities, internal facts, and safe receipts.
* `scripts/voice_pipecat_e2e_relay_invocation_process_provenance.py` for descriptor-derived execution-anchor capture, digesting, command/environment allowlists, and immediate pre-spawn revalidation.
* `scripts/voice_pipecat_e2e_relay_invocation_process_state.py` for the cap-one adapter registry, three role slots, operation gates, state conditions, replay records, quarantine, and total-absence predicate.
* `scripts/voice_pipecat_e2e_relay_invocation_process_local.py` as the sole local OS and loopback HTTP effect boundary: registered spawn, PID/PGID inspection, non-reaping Linux status observation, TERM/KILL, final reap, stream settlement, group checks, bounded readiness, and prebootstrap.
* `scripts/voice_pipecat_e2e_relay_invocation_process_lifecycle.py` for start/readiness/finish/stop transitions and return-loss reconciliation.
* `scripts/voice_pipecat_e2e_relay_invocation_process.py` for the private concrete driver/tools factory that adapts the existing callback signatures to the new authority. It exports nothing publicly and has `__all__ = []`.

Keep each new production module below 700 lines. Split by authority boundary rather than accumulating a process god object.

## Plan of Work

Checkpoint 1 adds no new concrete-factory, adapter-registry, nonce, subprocess, socket, or HTTP effect before the exact consumed-build gate. Existing private workspace preparation and the Next build remain intentionally pre-consume. Start with the P0 red tests for concrete-seam pre-consume refusal and the stronger global public refusal. Add a private exact `_RelayConcreteInvocationSelection` alongside the existing exact `RelayInvocationDriver` input in the outer driver state and replay validators. This selection is a module-owned singleton with no callback, clock, path, registry, nonce, or per-run authority. It can only select the later branch; code must not call into the concrete module until consumption. Then introduce the consumed-build grant and execution-anchor snapshot. Extend build evidence only as needed to retain authentic descriptor identities and digests already observed during workspace preparation/build validation. The factory validates the exact live consumed binding, outer owner, cleanup evidence, immutable deadline, and clock/wait domain before constructing a matched concrete driver/tools pair. Mixed, forged, copied, subclassed, expired, revoked, unconsumed, cross-key, or replay-mismatched inputs fail through fixed unchained errors without touching a new concrete effect seam.

The first checkpoint also adds deadline plumbing and child-bound readiness contracts without running them. Add an optional absolute start deadline to `RelayChildRequest`; synthetic requests may retain `None`, but concrete requests require the exact executor forward deadline. Add an exact `RelayStopRequest` carrying the cleanup deadline. The concrete invocation owner retains the validated cleanup timeout and clock identity, atomically mints its deadline on the first cleanup-required transition inside `_cleanup_invocation_locked()`, and supplies the same request to every driver stop callback; a caller may provide an already-minted canonical request on later outer settlement but cannot replace or extend it. Add private app and web readiness nonce digests to the request/environment contract. Extend the app health endpoint and add the dynamic Next health route. Keep existing schemas compatible for non-concrete tests. Private live canonical state may retain the exact commands, environments, paths, nonces, and HTTP request values while it owns them. Tests must prove those values never appear in outward `repr`, exception strings/arguments, causes/contexts, or production traceback locals, and that terminal observations, tombstones, failure history, and retired registries retain none of them after cleanup.

Checkpoint 2 implements the adapter's process authority. The per-role state machine is:

    PREOWNED
      -> START_INTENDED
      -> SPAWNING
      -> REGISTERED
      -> IDENTITY_BOUND
      -> RUNNING
      -> READINESS_WAIT        (app and web only)
      -> START_COMMITTED
      -> START_PUBLISHED

    browser after start:
      -> FINISH_INTENDED
      -> TERMINAL_ZERO_OBSERVED_UNREAPED
      -> LIVE_DESCENDANTS_ABSENT
      -> REAPED
      -> GROUP_ABSENT
      -> HANDLES_CLOSED
      -> RAW_CLEARED
      -> FINISH_COMMITTED
      -> FINISH_PUBLISHED

    stop from any phase:
      -> STOP_INTENDED
      -> LIVE_LEADER_IDENTITY_REVALIDATED
      -> TERM_INTENDED / TERM_OBSERVED
      -> LIVE_LEADER_IDENTITY_REVALIDATED
      -> KILL_INTENDED / KILL_OBSERVED when still live
      -> TERMINAL_OBSERVED_UNREAPED
      -> LIVE_DESCENDANTS_ABSENT
      -> REAPED
      -> GROUP_ABSENT
      -> HANDLES_CLOSED
      -> RAW_CLEARED
      -> STOP_COMMITTED
      -> STOP_PUBLISHED
      -> SCRUBBED / RETIRED

An early exit, timeout, nonzero browser result, invalid PID/PGID, PID reuse suspicion, process-group drift, handle failure, or ambiguous signal moves the authority to cleanup-required or quarantined state. It never emits a success receipt. A `ProcessLookupError` is only a candidate absence and is rechecked. A permission error or identity contradiction must not signal a possibly unrelated process. Once the leader is terminal, reaped, or cannot be freshly tied to the original group, the adapter performs observation only. It never sends TERM or KILL to the remembered PGID. On Linux, terminal success requires an unreaped-terminal observation plus proof that no live descendant remains, followed by reap and a final group-absence check. If that proof is unavailable or contradictory, quarantine is the only accepted state.

The sole spawn path calls `registered_popen_factory()` with the exact internally derived tuple, `executable=argv[0]`, exact `cwd`, a copied replacement environment, `stdin/stdout/stderr=DEVNULL`, `shell=False`, `close_fds=True`, `start_new_session=True`, and `umask=0o077`. Raw `Popen`, PID, PGID, descriptors, requests, and nonces remain inside the registered worker. No raw process object crosses into `RelayInvocationOwner` or a receipt. On Linux the local seam observes terminal status without reaping through `os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)` or an equivalently tested injected pidfd wrapper, inspects live process-group membership while the leader still anchors identity, then reaps once and requires the same exit status plus final group absence. `Popen.poll()` and `Popen.wait()` are forbidden before descendant-absence proof on this terminal path because both may reap. If WNOWAIT is unavailable or any wait fact contradicts the registered process, the role quarantines rather than approximating terminal state.

App readiness calls an exact literal-`127.0.0.1` health URL through the private proxy-disabled, redirect-disabled transport, with an exact method and Host header and a bounded response size. It requires the full relay health schema, the expected nonce digest, and the owned app alive with the same PID/PGID immediately before and after the response. Prebootstrap uses the same direct-loopback transport, the already-authenticated exact request and existing destination validator, caps the body, and revalidates app liveness around the call. Web readiness requires the force-dynamic exact schema and nonce digest plus both app and web alive before and after. Browser start revalidates app and web and publishes only after its own identity is bound and a non-reaping WNOWAIT observation reports it running. Browser finish revalidates app and web before accepting zero exit, and the relay owner revalidates them again before browser-result consumption and forward success. Spontaneous app or web exit after readiness therefore forces cleanup-only handling and cannot become a terminal observation.

Every effect follows intend, perform, observe, commit, publish. The committed fact is durable in the adapter registry before invoking an existing destination. If return is lost after the effect or publication, replay observes or republishes the same fact; it does not respawn, repeat an ambiguous signal, or create a second logical prebootstrap reservation. Phase-cut tests inject ordinary errors, `KeyboardInterrupt`, and `SystemExit(71)` before intent, after intent/before effect, after effect/before commit, and after commit/publication/before return. The earliest control remains authoritative after cleanup.

The lock nesting visible to the adapter is fixed:

    RelayProbeOwner operation lock
      -> RelayInvocationOwner operation lock
        -> adapter role-slot lock (brief lookup/publication only)
          -> role authority operation gate
            -> role state condition

The current synchronous lifecycle intentionally keeps the outer `RelayProbeOwner` and `RelayInvocationOwner` operation locks held while it calls the driver. This plan does not refactor that ownership. The narrower invariant is that no adapter role-slot lock, authority operation gate, role state condition, raw destination lock, or destination leaf lock is held across `Popen`, HTTP, sleep, non-reaping process inspection, signal, kill, join, final reap, handle close, or outward publication. Worker threads acquire only their private state condition and never acquire either outer owner lock, the authority operation gate, or call outward into an owner/destination. The adapter commits state, releases every adapter lock, and only then publishes outward. Existing cleanup remains browser, web, app.

Checkpoint 3 changes the private executor composition. The synthetic path remains byte-for-byte in behavior. Outer driver, attempt, replay, and terminal records store a caller selection field whose value is either the exact synthetic driver or `_RelayConcreteInvocationSelection`. For the sealed concrete selection, `_resolve_or_intend_inner_evidence()` first preowns one exact pair destination, then creates or recovers the grant, concrete tools, and concrete driver only after the exact consumed evidence and runtime deadline exist. Store-return or factory-return loss must reread that destination and the canonical registry and recover the same identities. Inner live evidence stores both caller selection and effective driver/tools fields; `_owner_binding()` receives only the effective pair. Terminal cleanup revokes and scrubs the effective pair, callbacks, grant, and pair destination before publishing the observation, while the outer terminal record retains only the inert selector tombstone and sanitized terminal descriptor for same-input replay. Inner cleanup cannot release built use until the adapter's total-absence predicate proves all role registries, raw destinations, workers, processes, handles, groups, live requests, pair/grant entries, and quarantine entries absent. Workspace deletion therefore cannot race a child whose executable or current directory still belongs to the workspace.

The fake end-to-end integration uses fake process, clock, wait, HTTP, group, and signal seams but the real outer executor, consumed-build transition, `RelayProbeOwner`, invocation lifecycle, reverse cleanup, and terminal observation. It asserts the exact ordering shown in `Purpose / Big Picture`, one spawn per role, no duplicate prebootstrap, browser-before-web-before-app stop, adapter absence before build use release, and full outer/path/port absence before the observation becomes readable.

Checkpoint 4 runs the gates and review. New focused tests are split into values/provenance, lifecycle, adversarial phase cuts, overlap, executor integration, and an optional Linux-only process-group conformance file. Extend existing public stack refusal tests and executor adversarial/replay tests rather than duplicating the already exhaustive generic invocation destination suite. Run an independent security/process review against the exact diff. Resolve every P0/P1 before pushing the code checkpoint.

Checkpoint 5 is effectful and requires separate authorization. Run only on disposable Linux/amd64 with the pinned Docker image and no host-network or private-aiortc fallback. The run is successful only if it proves the exact bridge/container/TLS identities, child-bound app/web readiness, relay-only selected candidate, same-allocation bidirectional media bytes, zero browser exit, sanitized evidence, artifact consumption/deletion, every process group absent, Docker/network/TLS cleanup, workspace/run-root deletion, port release, and all registries empty. Any partial or ambiguous result stays a fixed failure and leaves the public refusal in place.

Work can be parallelized only across read-only review or tests that touch disjoint files. The consumed-grant API, process authority, and executor integration are sequential because each establishes the authority used by the next. Once an API checkpoint is stable, one reviewer may audit provenance/secrecy while another audits process state/races. No two implementers should edit the same registry or state-machine module concurrently.

## Concrete Steps

Work from `/Users/swayam.gupta/Documents/GitHub/conv-ai-visual` on `codex/pipecat-relay-b0`. Before each checkpoint, prove the tree and remote relationship:

    git status --short --branch
    git fetch origin codex/pipecat-relay-b0
    git rev-parse HEAD
    git rev-parse origin/codex/pipecat-relay-b0

For Checkpoint 1, write the first boundary tests before production code:

    pytest -q -W error \
      tests/test_voice_pipecat_e2e_relay_invocation_process_values.py \
      tests/test_voice_pipecat_e2e_relay_linux_executor_concrete_adapter.py \
      tests/test_voice_pipecat_e2e_stack.py

The initial run should fail because the private grant/factory does not exist. After implementation, it must pass and show two distinct boundaries. A rejected private pre-consume selection may traverse the existing workspace/build transaction, but it cannot call the concrete factory, pair/grant destination, adapter registry, nonce generator, `registered_popen_factory`, or HTTP seam. The public relay refusal cannot call any of those or clock, UUID, path, Docker, or browser seams.

For Checkpoint 2, run the concrete process suites with warnings promoted to errors:

    pytest -q -W error \
      tests/test_voice_pipecat_e2e_relay_invocation_process_values.py \
      tests/test_voice_pipecat_e2e_relay_invocation_process_lifecycle.py \
      tests/test_voice_pipecat_e2e_relay_invocation_process_adversarial.py \
      tests/test_voice_pipecat_e2e_relay_invocation_process_overlap.py

The expected result is all tests passing with barrier-driven races and no sleep-based concurrency assertions. The adversarial matrix must cover ordinary failure, `KeyboardInterrupt`, and `SystemExit` at every committed effect/publication cut.

For Checkpoint 3, run the real composition through fake local seams plus the existing adjacent suites:

    pytest -q -W error \
      tests/test_voice_pipecat_e2e_relay_linux_executor_concrete_adapter.py \
      tests/test_voice_pipecat_e2e_relay_invocation.py \
      tests/test_voice_pipecat_e2e_relay_owner.py \
      tests/test_voice_pipecat_e2e_relay_owner_recovery.py \
      tests/test_voice_pipecat_e2e_relay_linux_executor.py \
      tests/test_voice_pipecat_e2e_relay_linux_executor_driver.py \
      tests/test_voice_pipecat_e2e_relay_linux_executor_driver_adversarial.py \
      tests/test_voice_pipecat_e2e_relay_linux_executor_driver_replay.py \
      tests/test_voice_pipecat_e2e_stack.py

Before any checkpoint commit, run the relevant static gates and inspect only the named diff:

    python3 -m ruff check scripts tests
    python3 -m ruff format --check scripts tests
    python3 -m py_compile scripts/voice_pipecat_e2e_relay_invocation_process*.py
    git diff --check
    git diff --stat
    git diff -- scripts/voice_pipecat_e2e_relay_invocation* scripts/voice_pipecat_e2e_relay_linux_executor* scripts/voice_pipecat_e2e_app.py web/src/app/e2e/voice tests

Keep production modules below the repository's 700-line cap:

    wc -l scripts/voice_pipecat_e2e_relay_invocation_process*.py

After focused and adjacent gates pass, run the complete repository suite from the same working tree:

    pytest -q -W error

If frontend readiness code changed, also run:

    npm --prefix web test
    npm --prefix web run typecheck
    npm --prefix web run lint
    npm --prefix web run build

Return to the repository root before committing. Stage only the checkpoint's named files, review `git diff --cached`, create one logical commit, and push with a normal fast-forward push. Never use `git add -A` or force push. Record the SHA, exact test counts, and truthful scope in this plan and the parent plan after every checkpoint.

The separately authorized Linux process-group conformance test, if added, runs only on Linux and spawns a tiny test helper with one descendant. It proves `setsid`, TERM/KILL escalation, reap, and group absence; it must be labeled non-qualifying. The final forced-relay command will be recorded here when the private qualification entrypoint exists and has passed static/public-boundary review. Do not invent a command or run effects before that checkpoint.

## Validation and Acceptance

Checkpoint 1 is accepted only when an exact live consumed-build evidence object can mint one canonical private grant and matched driver/tools pair through a caller-preowned recovery destination, while every forged, stale, expired, revoked, unconsumed, cross-key, mixed, copied, subclassed, or replay-changed input fails before every new concrete effect seam. The one intentional workspace `node_modules` symlink must match its original link identity and absolute target-tree provenance; execution anchors must reject any substitution of that link/target, workspace, CLI, package, config, test, lockfile, app entrypoint, Python, Node, or build output before spawn. Store/factory return loss must recover the same pair exactly once. `RelayInvocationOwner.concrete_adapter` and its safe `repr` must truthfully report only a canonical matched live concrete pair. Existing synthetic invocation tests must remain green.

Checkpoint 2 is accepted only when all three roles are preowned before the first spawn; `Popen` authority is registered before initialization; start-versus-stop yields only zero-spawn or one-spawn-then-proven-absence; finish-versus-stop cannot publish a false zero exit; a foreign health 200 cannot satisfy readiness; hostile proxy variables, redirects, and oversized HTTP responses cannot redirect or leak readiness/prebootstrap traffic; and a leader exit with a live descendant cannot publish finish or stop. No TERM/KILL may occur after terminal/reaped leader observation, identity drift, or an ambiguous signal fact. A zero leader exit plus a live descendant or PID/PGID-reuse candidate must produce zero later signals, no receipt, and quarantine. Tests must prove `waitid(..., WNOWAIT)` precedes descendant proof and reap, that `Popen.poll()`/`wait()` cannot run early, and that unavailable or contradictory WNOWAIT facts quarantine. The forward deadline and separately minted cleanup deadline must be exact, bounded, non-refreshing, and incapable of authorizing each other's phase. A child-spawned then readiness-failed phase cut must prove synchronous internal rollback mints a fresh bounded cleanup deadline before control returns outward and before the first stop call. Persistent or ambiguous process-group presence must retain quarantine and capacity.

The process coverage map is:

    concrete grant
      +-- invalid/pre-consume --------------------> fixed failure, zero adapter effects
      `-- authentic consumed build
           +-- anchor revalidation failure ------> fixed failure, zero spawn
           `-- adapter preowned
                +-- app start/readiness
                |    +-- foreign/malformed/early exit -> cleanup, no receipt
                |    `-- owned exact response --------> app start receipt
                +-- prebootstrap
                |    +-- proxy/redirect/oversize/ambiguous -> cleanup, no replayed mint
                |    `-- exact cached response -------> existing receipt
                +-- web start/readiness
                |    +-- app/web liveness loss -------> cleanup, no receipt
                |    `-- owned exact response --------> web start receipt
                +-- browser start/finish
                |    +-- app/web death/nonzero/live descendant -> cleanup, no exit receipt
                |    `-- zero+no live descendants+reap+group absence -> exit receipt
                `-- reverse stop
                     +-- uncertain identity/group ----> quarantine, no capacity
                     `-- browser/web/app absent ------> adapter retirement

Checkpoint 3 is accepted only when the full fake-local executor proves this happens-before chain:

    build consumed
      < concrete grant and pair
      < all role preownership
      < app start
      < prebootstrap
      < web start
      < browser start and finish
      < browser result consumption
      < browser/web/app stop
      < concrete adapter total absence
      < inner settlement
      < built-use release and revocation acknowledgement
      < workspace/run-root deletion
      < fixed-port and outer retirement
      < terminal observation read

The integration must also prove one spawn per role, one logical direct-loopback prebootstrap, no proxy use, no raw child output, first-control preservation, secret-free outward/terminal failures and tracebacks, live-value scrubbing before terminal observation, same-key selector tombstone replay without a live pair, cross-key exclusion, app/web liveness through browser result consumption, and capacity reuse only after total absence.

Repository acceptance requires the focused and adjacent suites, every repository test, Ruff lint/format, `py_compile`, frontend unit/type/lint/build gates when touched, `git diff --check`, module-size checks, and an independent review with no open P0/P1. The public refusal regression must retain its exact exit code and stderr and prove zero adapter import/call/effect edges.

The final disposable-Linux run is accepted only by real evidence, not mocks: pinned image/platform and source commit, exact `/29`, TLS listener/trust, relay-only browser policy and selected relay candidate, one or two source-correlated allocations, same-allocation bidirectional peer bytes, nonzero local and remote PCM, interruption and completion evidence, zero browser exit, artifact/evidence validation, and total process/Docker/network/TLS/path/port/registry cleanup. A failure is recorded as a failure. No incomplete run may set `qualification_verified=True` or remove the public refusal.

## Engineering Review Record

The 2026-08-29 architecture review found the existing invocation destination protocol reusable and recommended a private cap-one adapter behind the exact nominal driver/tools types. The security review blocked implementation unless consumed-build authority, descriptor-backed execution provenance, registered-before-init spawn, child-bound readiness, direct loopback without proxy/redirect, separate cleanup time, signal revocation after leader termination, group-absence receipts, secret-safe exact environments, and public-refusal isolation are hard gates. The test review supplied the phase-cut and overlap matrix captured above. Two later read-only reviews of this exact file caught and corrected deadline, signal, proxy, lock-scope, symlink, source/workspace, selector replay, secrecy, and post-readiness liveness ambiguities. This plan now adopts the stricter union of all reviews; there are no unresolved P0/P1 design findings before Checkpoint 1.

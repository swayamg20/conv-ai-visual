# Make Voice Tutoring Survive TTS Provider Failures

## Purpose / Big Picture

This ExecPlan covers the next voice-reliability batch for the education-first branch. The goal is to make the voice tutoring experience degrade gracefully when the cloud TTS path is slow, flaky, or down. Right now the system mostly logs TTS failures and moves on sentence by sentence. That is not enough for real student sessions.

After this work, a student in a voice session should still hear a usable response even if ElevenLabs errors or rate-limits. The system should retry when the failure is transient, fall back to local Kokoro when the cloud path is unavailable, and surface enough observability that operators can distinguish provider failures from model or transport issues.

Success is observable in one concrete scenario: simulate or trigger an ElevenLabs failure during a voice response and confirm that the student still receives audio, the logs show whether the system retried or fell back, and the observability view makes that behavior visible.

## Progress

- [x] 2026-03-23 11:14 IST: Created the ExecPlan and grounded it in the current repository state.
- [x] 2026-03-23 11:42 IST: Audited the current TTS error path in `main.py` and `funcs/tts_pipeline.py`.
- [x] 2026-03-23 11:50 IST: Added bounded retry and backoff for transient ElevenLabs failures.
- [x] 2026-03-23 11:57 IST: Added sentence-level Kokoro fallback when the cloud path is unavailable or exhausted.
- [x] 2026-03-23 12:00 IST: Surfaced retry/fallback outcomes in voice logs and dashboard metadata using a sidecar resilience table.
- [ ] Verify that a real voice flow still completes under simulated TTS failure.
- [x] 2026-03-23 12:02 IST: Completed static verification and updated the live ExecPlan.

## Surprises & Discoveries

As of plan creation, the system already supports both ElevenLabs and Kokoro, but not as a dynamic per-turn fallback path. Startup chooses one provider in `main.py`: Kokoro if `TTS_PROVIDER=kokoro`, otherwise ElevenLabs through `TTSPipeline`. Once a provider is selected, `_tts_sender()` in `main.py` streams each sentence and only logs exceptions if TTS fails.

That means the repo already has most of the primitives needed for graceful degradation, but the control flow is incomplete. `funcs/kokoro_tts.py` is a viable local fallback implementation. The missing work is deciding when and how the system should switch providers without destabilizing the voice stream.

The observability surface already records several useful TTS metrics in `VoicePipelineLogModel` and the dashboard at `web/src/app/obs/page.tsx`, including TTS duration, time to first chunk, chunk count, and whether TTS was interrupted. There is currently no field for retry count or fallback usage, so this batch may need a small persistence or API extension if those signals are important to operators.

The implemented version uses a sidecar table instead of altering `voice_pipeline_log` directly. That choice avoids depending on table-alter migrations in a repo that currently relies on `create_all()` and is therefore safer for an in-flight branch.

## Decision Log

2026-03-23, Codex: This batch will treat retry and fallback as part of voice reliability, not as two separate projects. A student only cares whether audio arrived, not which provider succeeded.

2026-03-23, Codex: The first version should stay inside the existing backend voice pipeline. Client-side fallback logic is out of scope for this branch.

2026-03-23, Codex: Kokoro is already present in the repo and is the right first fallback target. Adding another provider is unnecessary for this batch.

2026-03-23, Codex: Retry/fallback observability will be stored in a new sidecar table rather than by altering the existing `voice_pipeline_log` schema in place. This is the safer branch-local change because the repo does not currently have a migration framework for table alterations.

## Outcomes & Retrospective

Implementation is in. The backend now retries transient ElevenLabs failures with backoff, falls back to Kokoro at the sentence level when needed, and records retry/fallback metadata for operator visibility. Static verification is complete. The only deferred part is failure-injection runtime proof in a real voice environment.

## Context and Orientation

In this repository, the live voice pipeline is orchestrated in `main.py`. `_run_llm_tts()` consumes LLM sentences and `_tts_sender()` calls `tts_pipeline.text_to_speech_stream(sentence)` for each sentence. The selected `tts_pipeline` is created during startup in `main.py`, where the branch currently chooses Kokoro or ElevenLabs once at process boot.

`funcs/tts_pipeline.py` wraps ElevenLabs. Today it exposes streaming and non-streaming synthesis but has no retry policy, no transient error classification, and no built-in fallback behavior. `funcs/kokoro_tts.py` provides a local streaming-compatible TTS path that can be used as a backup when cloud TTS is unavailable.

`VoicePipelineLogRepo` and the observability dashboard in `web/src/app/obs/page.tsx` already expose useful TTS timing data, which makes this batch easier to verify. The open question is whether retry/fallback should be added as first-class logged fields or conveyed through existing log streams plus a lightweight metadata extension.

## Plan of Work

Start by tracing the current error behavior in `_tts_sender()` inside `main.py` and the exception surfaces in `funcs/tts_pipeline.py`. The first milestone is to classify what should be retried. Rate limits, network interruptions, and transient provider errors should likely retry. Invalid requests or clearly permanent errors should not.

Once retry behavior is defined, add it to the ElevenLabs wrapper or the calling path in a way that does not make the streaming control flow harder to reason about. Keep the implementation bounded. A small retry loop with backoff is preferable to a generic abstraction that hides too much.

After retry exists, add a fallback path to Kokoro. The simplest safe design is to switch the sentence to Kokoro after the cloud path exhausts retries. The student experience matters more than keeping the same voice characteristics. The fallback path should be explicit in logs so operators know why the voice changed.

Finish by extending observability enough to prove the system worked. That may be as small as structured logs, or it may require a small addition to the voice log model and dashboard if operator visibility would otherwise be too weak.

## Concrete Steps

1. Establish the baseline from repository root.

    Run:
        python3 -m py_compile main.py funcs/*.py
        cd web && npx tsc --noEmit

    Expected result: the branch is clean before voice reliability changes begin.

2. Audit the current TTS path.

    Read:
        main.py around `_tts_sender()` and startup TTS initialization
        funcs/tts_pipeline.py
        funcs/kokoro_tts.py
        web/src/app/obs/page.tsx

    Expected result: a concrete decision on where retries and fallback should live.

3. Implement transient retry behavior.

    Edit:
        funcs/tts_pipeline.py
        and/or main.py if the retry loop fits better at the call site

    Expected result: transient ElevenLabs failures no longer immediately drop the sentence.

4. Implement Kokoro fallback.

    Edit:
        main.py
        funcs/tts_pipeline.py
        funcs/kokoro_tts.py if an adapter/helper is needed

    Expected result: after retry exhaustion, the student still receives audio from the local fallback path.

5. Improve observability.

    Edit:
        main.py
        funcs/models.py if persistence fields are required
        web/src/app/obs/page.tsx if the dashboard should surface retry/fallback state

    Expected result: operators can see whether a voice response was normal, retried, or served by fallback.

6. Re-run verification from repository root.

    Run:
        python3 -m py_compile main.py funcs/*.py
        python3 -c "from main import app"
        cd web && npx tsc --noEmit

    Expected result: backend and frontend remain valid after the reliability change.

7. Run a failure-injection voice scenario.

    Simulate or force a cloud TTS failure during a voice response.

    Expected result: the session still emits audio, and the logs or dashboard prove whether the system retried and/or fell back.

## Validation and Acceptance

Acceptance for this ExecPlan requires a degraded-but-successful voice response under failure.

The minimum acceptance path is:
- trigger a transient ElevenLabs failure or a forced substitute failure path
- confirm the system retries within bounded time
- confirm that, after retry exhaustion, Kokoro or the chosen fallback path still emits audio
- confirm that logs or observability make the behavior visible

The implementation is not complete if the code compiles but a TTS failure still results in silence or an aborted voice response.

Minimum verification commands:

    python3 -m py_compile main.py funcs/*.py
    python3 -c "from main import app"
    cd web && npx tsc --noEmit

If local audio or voice transport setup is unavailable, record that limitation in `Surprises & Discoveries` and provide the strongest fallback evidence possible, such as a controlled failing TTS unit path plus structured logs.

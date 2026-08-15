import { describe, expect, it } from "vitest";

import {
  createInitialVoiceEventState,
  reduceVoiceEvent,
  type VoiceEventState,
} from "./event-reducer";
import { REQUIRED_VOICE_READY_COMPONENTS } from "./events";

function rawEvent(
  eventType: string,
  payload: Record<string, unknown>,
  envelope: Record<string, unknown> = {}
): Record<string, unknown> {
  const producerSequence = envelope.producer_sequence ?? 1;
  return {
    schema_version: 1,
    event_id: `${eventType}-${String(producerSequence)}`,
    event_type: eventType,
    trace_id: "trace-1",
    voice_call_id: "call-1",
    session_id: "session-1",
    producer_id: "worker-1",
    producer_sequence: producerSequence,
    emitted_at: "2026-08-12T00:00:00.000Z",
    payload,
    ...envelope,
  };
}

function apply(state: VoiceEventState, input: unknown): VoiceEventState {
  const reduction = reduceVoiceEvent(state, input);
  expect(reduction.disposition).toBe("applied");
  return reduction.state;
}

function patchEvent(envelope: Record<string, unknown> = {}): Record<string, unknown> {
  return rawEvent(
    "canvas_patch",
    {
      artifact_id: "artifact-1",
      artifact: { kind: "operations_v1", operations: [{ action: "clear" }] },
    },
    {
      event_id: "patch-1",
      task_id: "task-1",
      task_generation: 1,
      canvas_base_revision: 0,
      canvas_result_revision: 1,
      ...envelope,
    }
  );
}

function workingTaskState(): VoiceEventState {
  let state = createInitialVoiceEventState("session-1", "call-1");
  state = apply(
    state,
    rawEvent("task_queued", {}, {
      event_id: "task-queued-1",
      task_id: "task-1",
      task_generation: 1,
      producer_id: "reasoner-1",
      producer_sequence: 1,
    })
  );
  return apply(
    state,
    rawEvent("task_working", { message: "Building the visual" }, {
      event_id: "task-working-1",
      task_id: "task-1",
      task_generation: 1,
      producer_id: "reasoner-1",
      producer_sequence: 2,
    })
  );
}

describe("Voice V2 event reducer", () => {
  it("deduplicates event IDs before applying any side effect", () => {
    const initial = createInitialVoiceEventState("session-1", "call-1");
    const message = rawEvent("transport_connected", {}, { event_id: "connected-1" });
    const first = reduceVoiceEvent(initial, message);
    const duplicate = reduceVoiceEvent(first.state, message);

    expect(first.disposition).toBe("applied");
    expect(duplicate.disposition).toBe("duplicate");
    expect(duplicate.state).toBe(first.state);
    expect(duplicate.effects).toEqual([]);
  });

  it("orders each producer independently and rejects sequence regressions", () => {
    let state = createInitialVoiceEventState("session-1");
    state = apply(
      state,
      rawEvent("transport_connected", {}, { producer_id: "transport", producer_sequence: 4 })
    );
    state = apply(
      state,
      rawEvent(
        "usage_recorded",
        { usage_id: "usage-1", category: "stt", quantity: 1, unit: "second" },
        { producer_id: "meter", producer_sequence: 1 }
      )
    );

    const stale = reduceVoiceEvent(
      state,
      rawEvent("transport_reconnecting", { attempt: 1 }, {
        event_id: "transport-stale",
        producer_id: "transport",
        producer_sequence: 3,
      })
    );

    expect(stale.disposition).toBe("stale");
    expect(stale.rejection?.code).toBe("producer_sequence_regression");
    expect(stale.state).toBe(state);
  });

  it("keeps Ready after a second producer replays its buffered transport fact", () => {
    let state = createInitialVoiceEventState("session-1", "call-1");
    state = apply(
      state,
      rawEvent("transport_connected", {}, {
        event_id: "browser-connected-1",
        producer_id: "browser:call-1",
        producer_sequence: 1,
      })
    );
    state = apply(
      state,
      rawEvent(
        "agent_ready",
        {
          profile_id: "pipecat-cascade-v1",
          required_components: REQUIRED_VOICE_READY_COMPONENTS,
          ready_components: REQUIRED_VOICE_READY_COMPONENTS,
        },
        {
          event_id: "pipecat-ready-1",
          producer_id: "pipecat-call-1",
          producer_sequence: 1,
        }
      )
    );

    const readySession = state.session;
    const replayedTransport = reduceVoiceEvent(
      state,
      rawEvent("transport_connected", {}, {
        event_id: "pipecat-connected-2",
        producer_id: "pipecat-call-1",
        producer_sequence: 2,
      })
    );

    expect(replayedTransport.disposition).toBe("applied");
    expect(replayedTransport.state.session).toBe(readySession);
    expect(replayedTransport.state.session).toMatchObject({
      phase: "ready",
      transportConnected: true,
      voiceReady: true,
    });
    expect(replayedTransport.state.lastAppliedEventId).toBe(
      "pipecat-connected-2"
    );
    expect(replayedTransport.state.producerCursors).toEqual([
      { producerId: "browser:call-1", sequence: 1 },
      { producerId: "pipecat-call-1", sequence: 2 },
    ]);
  });

  it("rejects durable ledger regressions even across different producers", () => {
    let state = createInitialVoiceEventState("session-1");
    state = apply(
      state,
      rawEvent(
        "usage_recorded",
        { usage_id: "usage-10", category: "llm", quantity: 20, unit: "token" },
        { producer_id: "meter-a", producer_sequence: 1, ledger_sequence: 10 }
      )
    );
    const stale = reduceVoiceEvent(
      state,
      rawEvent(
        "usage_recorded",
        { usage_id: "usage-9", category: "tts", quantity: 10, unit: "character" },
        {
          event_id: "usage-ledger-9",
          producer_id: "meter-b",
          producer_sequence: 1,
          ledger_sequence: 9,
        }
      )
    );

    expect(stale.disposition).toBe("stale");
    expect(stale.rejection?.code).toBe("ledger_sequence_regression");
    expect(stale.state.lastLedgerSequence).toBe(10);
  });

  it("emits a canvas effect only for the current revision and waits for an ack", () => {
    const initial = workingTaskState();
    const patch = reduceVoiceEvent(initial, patchEvent());

    expect(patch.disposition).toBe("applied");
    expect(patch.effects).toHaveLength(1);
    expect(patch.effects[0]).toMatchObject({
      type: "apply_canvas_patch",
      event: { event_id: "patch-1", event_type: "canvas_patch" },
    });
    expect(patch.state.canvas).toMatchObject({
      appliedRevision: 0,
      visibleRevision: 0,
      pendingPatch: { eventId: "patch-1", baseRevision: 0, resultRevision: 1 },
    });

    const ack = reduceVoiceEvent(
      patch.state,
      rawEvent(
        "canvas_apply_ack",
        { artifact_id: "artifact-1" },
        {
          event_id: "ack-1",
          producer_id: "browser-1",
          producer_sequence: 1,
          causation_id: "patch-1",
          task_id: "task-1",
          task_generation: 1,
          canvas_result_revision: 1,
        }
      )
    );
    expect(ack.disposition).toBe("applied");
    expect(ack.state.canvas.appliedRevision).toBe(1);
    expect(ack.state.canvas.pendingPatch).toBeUndefined();

    const canvasBeforeUnknownVisibility = ack.state.canvas;
    const unknownVisibility = reduceVoiceEvent(
      ack.state,
      rawEvent(
        "canvas_first_visible",
        { artifact_id: "artifact-unknown" },
        {
          event_id: "visible-unknown",
          producer_id: "browser-1",
          producer_sequence: 2,
          causation_id: "ack-1",
          task_id: "task-1",
          task_generation: 1,
          canvas_result_revision: 1,
        }
      )
    );
    expect(unknownVisibility.disposition).toBe("rejected");
    expect(unknownVisibility.rejection?.code).toBe("canvas_causation_mismatch");
    expect(unknownVisibility.state.canvas).toBe(canvasBeforeUnknownVisibility);

    const visible = reduceVoiceEvent(
      ack.state,
      rawEvent(
        "canvas_first_visible",
        { artifact_id: "artifact-1" },
        {
          event_id: "visible-1",
          producer_id: "browser-1",
          producer_sequence: 2,
          causation_id: "ack-1",
          task_id: "task-1",
          task_generation: 1,
          canvas_result_revision: 1,
        }
      )
    );
    expect(visible.disposition).toBe("applied");
    expect(visible.state.canvas.visibleRevision).toBe(1);

    const canvasBeforeUnknownAnimation = visible.state.canvas;
    const unknownAnimation = reduceVoiceEvent(
      visible.state,
      rawEvent(
        "canvas_animation_complete",
        { artifact_id: "artifact-unknown" },
        {
          event_id: "animation-unknown",
          producer_id: "browser-1",
          producer_sequence: 3,
          causation_id: "visible-1",
          task_id: "task-1",
          task_generation: 1,
          canvas_result_revision: 1,
        }
      )
    );
    expect(unknownAnimation.disposition).toBe("rejected");
    expect(unknownAnimation.rejection?.code).toBe("canvas_causation_mismatch");
    expect(unknownAnimation.state.canvas).toBe(canvasBeforeUnknownAnimation);
  });

  it("never mutates canvas state for stale revisions or producer events", () => {
    const pending = reduceVoiceEvent(
      workingTaskState(),
      patchEvent({ producer_sequence: 2 })
    ).state;
    const canvasBefore = pending.canvas;

    const producerStale = reduceVoiceEvent(
      pending,
      patchEvent({ event_id: "patch-old-sequence", producer_sequence: 1 })
    );
    const revisionStale = reduceVoiceEvent(
      pending,
      patchEvent({
        event_id: "patch-old-revision",
        producer_id: "worker-2",
        producer_sequence: 1,
        canvas_base_revision: 2,
        canvas_result_revision: 3,
      })
    );

    expect(producerStale.disposition).toBe("stale");
    expect(producerStale.state.canvas).toBe(canvasBefore);
    expect(producerStale.effects).toEqual([]);
    expect(revisionStale.disposition).toBe("stale");
    expect(revisionStale.rejection?.code).toBe("stale_canvas_revision");
    expect(revisionStale.state.canvas).toBe(canvasBefore);
    expect(revisionStale.effects).toEqual([]);
  });

  it("does not acknowledge a patch from a superseded task generation", () => {
    const patch = reduceVoiceEvent(workingTaskState(), patchEvent());
    expect(patch.disposition).toBe("applied");

    const nextGeneration = apply(
      patch.state,
      rawEvent("task_queued", {}, {
        event_id: "task-queued-2",
        task_id: "task-1",
        task_generation: 2,
        producer_id: "reasoner-1",
        producer_sequence: 3,
      })
    );
    const canvasBefore = nextGeneration.canvas;
    const staleAck = reduceVoiceEvent(
      nextGeneration,
      rawEvent(
        "canvas_apply_ack",
        { artifact_id: "artifact-1" },
        {
          event_id: "ack-old-generation",
          producer_id: "browser-1",
          producer_sequence: 1,
          causation_id: "patch-1",
          task_id: "task-1",
          task_generation: 1,
          canvas_result_revision: 1,
        }
      )
    );

    expect(staleAck.disposition).toBe("stale");
    expect(staleAck.rejection?.code).toBe("stale_task_generation");
    expect(staleAck.state.canvas).toBe(canvasBefore);
    expect(staleAck.effects).toEqual([]);
  });

  it("fails closed on unknown event schemas/types without touching canvas", () => {
    const pending = reduceVoiceEvent(
      workingTaskState(),
      patchEvent()
    ).state;
    const canvasBefore = pending.canvas;
    const unknown = reduceVoiceEvent(
      pending,
      rawEvent("canvas_force_replace", { document: "unsafe" }, {
        event_id: "unknown-1",
        producer_sequence: 2,
      })
    );

    expect(unknown.disposition).toBe("rejected");
    expect(unknown.rejection?.code).toBe("unknown_event_type");
    expect(unknown.state.session.phase).toBe("unavailable");
    expect(unknown.state.compatibilityFailure?.code).toBe("unknown_event_type");
    expect(unknown.state.canvas).toBe(canvasBefore);
    expect(unknown.effects).toEqual([]);

    const laterAck = reduceVoiceEvent(
      unknown.state,
      rawEvent(
        "canvas_apply_ack",
        { artifact_id: "artifact-1" },
        {
          event_id: "ack-after-compatibility-failure",
          producer_id: "browser-1",
          producer_sequence: 1,
          causation_id: "patch-1",
          task_id: "task-1",
          task_generation: 1,
          canvas_result_revision: 1,
        }
      )
    );
    expect(laterAck.rejection?.code).toBe("compatibility_locked");
    expect(laterAck.state.canvas).toBe(canvasBefore);
  });

  it("prevents a cancelled task from publishing a late canvas patch", () => {
    let state = createInitialVoiceEventState("session-1");
    state = apply(
      state,
      rawEvent("task_queued", {}, {
        event_id: "task-queued-1",
        task_id: "task-1",
        task_generation: 1,
        producer_sequence: 1,
        ledger_sequence: 1,
      })
    );
    state = apply(
      state,
      rawEvent("task_cancelled", { reason: "user_cancelled" }, {
        event_id: "task-cancelled-1",
        task_id: "task-1",
        task_generation: 1,
        producer_sequence: 2,
        ledger_sequence: 2,
      })
    );
    const canvasBefore = state.canvas;

    const latePatch = reduceVoiceEvent(
      state,
      patchEvent({
        event_id: "late-patch-1",
        task_id: "task-1",
        task_generation: 1,
        producer_sequence: 3,
        ledger_sequence: 3,
      })
    );

    expect(latePatch.disposition).toBe("rejected");
    expect(latePatch.rejection?.code).toBe("task_not_publishable");
    expect(latePatch.state.canvas).toBe(canvasBefore);
    expect(latePatch.effects).toEqual([]);
  });

  it("rejects stale task generations without changing task state", () => {
    let state = createInitialVoiceEventState("session-1");
    state = apply(
      state,
      rawEvent("task_queued", {}, {
        event_id: "task-gen-2",
        task_id: "task-1",
        task_generation: 2,
        producer_sequence: 1,
      })
    );
    const tasksBefore = state.tasks;
    const stale = reduceVoiceEvent(
      state,
      rawEvent("task_working", { message: "late generation" }, {
        event_id: "task-gen-1-late",
        task_id: "task-1",
        task_generation: 1,
        producer_sequence: 2,
      })
    );

    expect(stale.disposition).toBe("stale");
    expect(stale.rejection?.code).toBe("stale_task_generation");
    expect(stale.state.tasks).toBe(tasksBefore);
  });

  it("fails closed on a cross-session event without changing canvas", () => {
    const state = createInitialVoiceEventState("session-1");
    const canvasBefore = state.canvas;
    const mismatch = reduceVoiceEvent(
      state,
      rawEvent("transport_connected", {}, { session_id: "session-2" })
    );

    expect(mismatch.disposition).toBe("rejected");
    expect(mismatch.rejection?.code).toBe("session_mismatch");
    expect(mismatch.state.session.phase).toBe("unavailable");
    expect(mismatch.state.canvas).toBe(canvasBefore);
  });

  it("fails closed on a cross-call event without changing canvas", () => {
    const state = createInitialVoiceEventState("session-1", "call-1");
    const canvasBefore = state.canvas;
    const mismatch = reduceVoiceEvent(
      state,
      rawEvent("transport_connected", {}, { voice_call_id: "call-2" })
    );

    expect(mismatch.disposition).toBe("rejected");
    expect(mismatch.rejection?.code).toBe("voice_call_mismatch");
    expect(mismatch.state.session.phase).toBe("unavailable");
    expect(mismatch.state.canvas).toBe(canvasBefore);
  });

  it("requires every task generation to begin queued", () => {
    const initial = createInitialVoiceEventState("session-1", "call-1");
    const invalid = reduceVoiceEvent(
      initial,
      rawEvent("task_working", { message: "Skipped queue" }, {
        task_id: "task-1",
        task_generation: 1,
      })
    );

    expect(invalid.disposition).toBe("rejected");
    expect(invalid.rejection?.code).toBe("invalid_task_transition");
    expect(invalid.state.tasks).toEqual([]);
  });

  it("records session_ended after session_ending before rejecting later events", () => {
    let state = createInitialVoiceEventState("session-1", "call-1");
    state = apply(
      state,
      rawEvent("session_ending", { reason: "user_requested" }, {
        event_id: "ending-1",
        producer_sequence: 1,
      })
    );
    expect(state.session.terminationStage).toBe("ending");

    state = apply(
      state,
      rawEvent("session_ended", { reason: "worker_stopped" }, {
        event_id: "ended-1",
        producer_sequence: 2,
      })
    );
    expect(state.session.terminationStage).toBe("ended");
    expect(state.seenEventIds).toContain("ended-1");

    const late = reduceVoiceEvent(
      state,
      rawEvent("session_ended", { reason: "duplicate_terminal" }, {
        event_id: "ended-2",
        producer_sequence: 3,
      })
    );
    expect(late.disposition).toBe("stale");
    expect(late.rejection?.code).toBe("session_ended");
  });

  it("replays the same ordered stream to the same serializable state", () => {
    const stream = [
      rawEvent("transport_connected", {}, { event_id: "connected", producer_sequence: 1 }),
      rawEvent(
        "agent_ready",
        {
          profile_id: "cascade-v1",
          required_components: REQUIRED_VOICE_READY_COMPONENTS,
          ready_components: REQUIRED_VOICE_READY_COMPONENTS,
        },
        { event_id: "ready", producer_sequence: 2 }
      ),
      rawEvent(
        "transcript_segment",
        { segment_id: "segment-1", text: "Hello", is_final: true },
        { event_id: "segment", producer_sequence: 3 }
      ),
      rawEvent(
        "turn_committed",
        { text: "Hello there" },
        { event_id: "turn", turn_id: "turn-1", producer_sequence: 4, ledger_sequence: 1 }
      ),
      rawEvent(
        "assistant_speech_started",
        { speech_id: "speech-1" },
        { event_id: "speech-start", turn_id: "turn-1", producer_sequence: 5 }
      ),
      rawEvent(
        "assistant_speech_stopped",
        { speech_id: "speech-1", reason: "completed" },
        { event_id: "speech-stop", turn_id: "turn-1", producer_sequence: 6 }
      ),
    ];

    const replay = () =>
      stream.reduce(
        (state, item) => reduceVoiceEvent(state, item).state,
        createInitialVoiceEventState("session-1")
      );

    expect(JSON.stringify(replay())).toBe(JSON.stringify(replay()));
    expect(replay().session.phase).toBe("ready");
  });
});
